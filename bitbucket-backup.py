#!/usr/bin/env python
import argparse
import datetime
import os
import subprocess
import sys
import re
import base64
import time
import logging
import traceback
from getpass import getpass
from urllib.parse import quote

import requests
from requests.auth import HTTPBasicAuth


_cmd_silent = False

# logger setup
_logger = logging.getLogger('bitbucket-backup')
_logger.setLevel(logging.DEBUG)
_logger_ch = logging.StreamHandler()
_logger_ch.setLevel(logging.INFO)
_logger_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
_logger_ch.setFormatter(_logger_formatter)
_logger.addHandler(_logger_ch)


class MaxBackupAttemptsReached(Exception):
    pass

def exec_cmd(command, stop_on_error: bool = True, valid_return_list: list = []) -> int:
    """
    Executes an external command taking into account errors and logging.
    """
    global _cmd_silent
    # Mask the token value while keeping username structure
    command_masked = re.sub(r"(x-bitbucket-api-token-auth:)[^@]+", r"\1*****", command)
    command_masked = re.sub(r"(Authorization:\s*Basic\s+)[^\"]+", r"\1*****", command_masked)
    _logger.debug(f"Executing command: {command_masked}")
    if _cmd_silent:
        if sys.platform.startswith('win'):
            command = f"{command} > nul 2> nul"
        else:
            command = f"{command} > /dev/null 2>&1"
    resp = subprocess.call(command, shell=True)
    if resp != 0 and (resp not in valid_return_list):
        if stop_on_error:
            _logger.error(f"Command [{command_masked}] failed")
            sys.exit(1)
        else:
            _logger.warning(f"Command [{command_masked}] failed: {resp}")
    return resp


def compress(repo, location: str) -> None:
    """
    Creates a TAR.GZ file with all contents cloned by this script.
    """
    if sys.platform.startswith('win'):
        return
    os.chdir(location)
    _logger.info("Compressing repositories in [%s]..." % location)
    exec_cmd(
        "tar -zcvf bitbucket-backup-%s-%s.tar.gz `ls -d *`"
        % (
            repo.get("owner").get("username") or repo.get("owner").get("nickname"),
            datetime.datetime.now().strftime("%Y%m%d%H%m%s"),
        )
    )
    _logger.info("Cleaning up...")
    for d in os.listdir(location):
        path = os.path.join(location, d)
        if os.path.isdir(path):
            exec_cmd("rm -rfv %s" % path)

def build_api_token_header(api_token: str) -> str:
    if api_token is None:
        _logger.info("build_api_auth_header - api_token is None!")
        return ''
    raw_auth = f"x-bitbucket-api-token-auth:{api_token}"
    b64_auth = base64.b64encode(raw_auth.encode("utf-8")).decode("utf-8")
    auth_header = f"Authorization: Basic {b64_auth}"
    # use with: '-c "http.extraHeader={auth_header}"'
    # or with : '-c "http.https://bitbucket.org/.extraHeader={auth_header}"'
    return auth_header

def clean_api_creds_from_repo(backup_dir: str) -> None:
    _logger.info('Cleaning repo of saved credentials')
    # clear saved HTTP creds from REPO
    exec_cmd(f'git -C "{backup_dir}" config --local --unset-all http.extraHeader', stop_on_error=False, valid_return_list=[5])
    exec_cmd(f'git -C "{backup_dir}" config --local --unset-all http.https://bitbucket.org/.extraHeader', stop_on_error=False, valid_return_list=[5])


def fetch_lfs_content(backup_dir: str, api_token: str = None, http: bool = False):
    _logger.info("Fetching LFS content...")
    os.chdir(backup_dir)
    command = "git lfs fetch --all"

    if http:
        command = f'git -c "http.https://bitbucket.org/.extraHeader={build_api_token_header(api_token)}" lfs fetch --all'

    retry_counter = 0
    while True:
        resp = exec_cmd(command, stop_on_error=False)
        if resp == 0:
            break
        else:
            retry_counter += 1
            if retry_counter >= 3:
                _logger.error('error Fetching LFS Content... Giving Up')
                break
            else:
                _logger.error(f'error Fetching LFS Content... Retry attempt {retry_counter}')
                time.sleep(1)


def get_repositories(
        username: str = None,
        user_email: str = None,
        api_token: str = None,
        team: str = None
):
    auth = None
    repos = []
    try:
        if all((user_email, api_token)):
            auth = HTTPBasicAuth(user_email, api_token)

        if auth is None:
            _logger.error("Must provide user_email and api_token")
            sys.exit(1)

        if not team and not username:
            # repository API needs a workspace(team) or username, we only have the email
            # attempt to lookup the username from the User API with the email address
            # this requires the 'read:user:bitbucket' permission on the api token
            _logger.info("Fetching User Info")
            #response = requests.get("https://api.bitbucket.org/2.0/user", auth=auth)
            response = requests.get("https://api.bitbucket.org/2.0/user?fields=username", auth=auth)
            if response.status_code == 401:
                _logger.error("Unauthorized! Check your credentials and try again.\nDoes your api token have the 'read:user:bitbucket' permission?\nor specify the -u/--username option to bypass the 'read:user:bitbucket' permission requirement")
                sys.exit(1)
            username = response.json().get("username")
        
        # url = "https://api.bitbucket.org/2.0/repositories/{}".format(team or username)
        url = "https://api.bitbucket.org/2.0/repositories/{}?fields=next,values.name,values.slug,values.workspace.slug,values.owner.username,values.has_wiki,&pagelen=100".format(team or username)

        _logger.info("Fetching Repository List...please wait...")
        response = requests.get(url, auth=auth)
        response.raise_for_status()
        repos_data = response.json()

        for repo in repos_data.get("values"):
            repos.append(repo)
        while repos_data.get("next"):
            response = requests.get(repos_data.get("next"), auth=auth)
            repos_data = response.json()
            for repo in repos_data.get("values"):
                repos.append(repo)
    except requests.exceptions.RequestException as e:
        if e.response.status_code == 401:
            _logger.error("Error Fetching Repository List - 401 - check email and api token")
            sys.exit(1)
        else:
            _logger.error(f"Error Fetching Repository List - {e.response.status_code}")
            _logger.error(traceback.format_exc())
            sys.exit(1)
            
    _logger.info(f"Found {len(repos)} Repositories")
    return repos


def clone_repo(
    repo,
    backup_dir: str,
    http: bool,
    api_token: str,
    mirror: bool = False,
    with_wiki: bool = False,
    fetch_lfs: bool = False,
) -> None:

    slug = repo.get("slug")
    #owner = repo.get("owner").get("username") or repo.get("owner").get("nickname")
    owner = repo.get("workspace").get("slug") or repo.get("owner").get("username")
    owner_url = quote(owner, safe="@")

    if http and not api_token:
        _logger.error("Cannot backup via http without api_token")
        sys.exit(1)

    slug_url = quote(slug)

    git_command = "git clone"
    if mirror:
        git_command = "git clone --mirror"
    if http:
        git_command = f'{git_command} -c "http.https://bitbucket.org/.extraHeader={build_api_token_header(api_token)}"'

        command = f"{git_command} https://bitbucket.org/{owner_url}/{slug_url}.git"
    else:
        command = f"{git_command} git@bitbucket.org:{owner_url}/{slug_url}.git"

    _logger.info(f"Cloning {repo.get("name")}...")
    exec_cmd(f'{command} "{backup_dir}"')

    if http:
        # !! This cleaning block needs to run before fetch_lfs_content()  !!
        # fetch_lfs_content() failure message:
        #   batch response: Client error: https://bitbucket.org/<owner>/<slug>.git/info/lfs/objects/batch
        #   error: failed to fetch some objects from 'https://bitbucket.org/<owner>/<slug>.git/info/lfs'

        clean_api_creds_from_repo(backup_dir)

        # ensure no passwords are saved into remote URL
        owner = repo.get("workspace").get("slug")
        owner_url = quote(owner, safe="@")
        slug = repo.get("slug")
        slug_url = quote(slug)
        exec_cmd(f'git -C "{backup_dir}" remote set-url origin https://bitbucket.org/{owner_url}/{slug_url}.git', stop_on_error=False)

    if fetch_lfs:
        fetch_lfs_content(backup_dir, api_token, http)
        
    if with_wiki and repo.get("has_wiki"):
        _logger.info(f"Cloning {repo.get('name')}'s Wiki...")
        exec_cmd(f'{command}/wiki "{backup_dir}_wiki"')



def update_repo(
        repo,
        backup_dir: str,
        http: bool,
        api_token: str,
        with_wiki: bool = False,
        prune: bool = False,
        fetch_lfs: bool = False
) -> None:

    os.chdir(backup_dir)

    command = "git remote update"

    if http:
        raw_auth = f"x-bitbucket-api-token-auth:{api_token}"
        b64_auth = base64.b64encode(raw_auth.encode("utf-8")).decode("utf-8")
        auth_header = f"Authorization: Basic {b64_auth}"
        command = f'git -c "http.extraHeader={auth_header}" remote update'

    if prune:
        command = f"{command} --prune"

    _logger.info(f"Updating {repo.get('name')}...")
    exec_cmd(command)

    if fetch_lfs:
        fetch_lfs_content(backup_dir, api_token, http)

    if with_wiki and repo.get("has_wiki"):
        wiki_dir = f"{backup_dir}_wiki"
        if os.path.isdir(wiki_dir):
            os.chdir(wiki_dir)
            _logger.info(f"Updating {repo.get('name')}'s Wiki...")
            exec_cmd(command)

    if http:
        clean_api_creds_from_repo(backup_dir)

        # ensure no passwords are saved into remote URL
        owner = repo.get("workspace").get("slug")
        owner_url = quote(owner, safe="@")
        slug = repo.get("slug")
        slug_url = quote(slug)
        exec_cmd(f'git -C "{backup_dir}" remote set-url origin https://bitbucket.org/{owner_url}/{slug_url}.git', stop_on_error=False)


def main():
    parser = argparse.ArgumentParser(description="Usage: %prog [options] ")
    parser.add_argument("-u", "--username", dest="username", help="Bitbucket username, i.e. johndoe [Optional requires 'read:user:bitbucket' scope on api token if missing]")
    parser.add_argument("-e", "--email", dest="user_email", help="Bitbucket email, i.e. johndoe@example.com [REQUIRED]")
    parser.add_argument("-p", "--api-token", dest="api_token", help="Bitbucket API Token [REQUIRED]")
    parser.add_argument("-t", "--team", dest="team", help="Bitbucket team")
    parser.add_argument("-l", "--location", dest="location", help="Local backup location")
    parser.add_argument("-x", "--non-interactive", dest="non_interactive", default=False, action='store_true', help="Non-Interactive, do not ask for info, will exit if missing options")
    parser.add_argument("--debug", dest="debug", default=False, action='store_true', help='Enable Debug logging')
    parser.add_argument("-s", "--cmd-silent", dest="cmd_silent", default=False, action='store_true', help="do not show command output, i.e. from git")
    parser.add_argument(
        "-c",
        "--compress",
        action="store_true",
        dest="compress",
        help="Creates a compressed file with all cloned repositories (cleans up location directory) [only available on unix OS]",
    )
    parser.add_argument(
        "-a",
        "--attempts",
        dest="attempts",
        type=int,
        default=3,
        help="max. number of attempts to backup repository",
    )
    parser.add_argument(
        "--mirror",
        action="store_true",
        help="Clone just bare repositories with git clone --mirror)",
    )
    parser.add_argument(
        "--fetchlfs",
        action="store_true",
        help="Fetch LFS content after clone/pull",
    )
    parser.add_argument("--with-wiki", dest="with_wiki", action="store_true", help="Includes wiki")
    parser.add_argument("--http", action="store_true", help="Fetch via https instead of SSH")
    parser.add_argument(
        "--skip-password",
        dest="skip_password",
        action="store_true",
        help="Ignores password prompting if no password is provided (for public repositories)",
    )
    parser.add_argument("--prune", dest="prune", action="store_true", help="Prune repo on remote update")

    parser.add_argument(
        "--ignore-repo-list",
        dest="ignore_repo_list",
        nargs="+",
        type=str,
        help="specify list of repo slug names to skip",
    )
    parser.add_argument(
        "--only-repos",
        dest="repo_whitelist",
        nargs="+",
        type=str,
        help="specify list of repo slug names to download",
    )

    args = parser.parse_args()
    if args.debug:
        _logger_ch.setLevel(logging.DEBUG)
    
    location = args.location
    username = args.username
    user_email = args.user_email
    api_token = args.api_token
    repo_whitelist = args.repo_whitelist
    http = args.http
    max_attempts = args.attempts
    global _cmd_silent
    _cmd_silent = args.cmd_silent
    _mirror = args.mirror
    _fetchlfs = args.fetchlfs
    _with_wiki = args.with_wiki
    team = args.team
    non_interactive = args.non_interactive

    _logger.info("bitbucket-backup tool")

    #if not username:
    #    username = input("Enter bitbucket username: ")
    if not user_email:
        if non_interactive:
            _logger.warning("Bitbucket email required! (-e / --email)")
            sys.exit(1)
        try:
            user_email = input("Enter bitbucket email: ")
        except KeyboardInterrupt, SystemExit:
            return

    # check environment for 'BITBUCKET_API_TOKEN'
    if not api_token:
        if "BITBUCKET_API_TOKEN" in os.environ:
            _logger.info("found environment variable 'BITBUCKET_API_TOKEN'")
            api_token = os.environ['BITBUCKET_API_TOKEN']

    if not api_token:
        if non_interactive:
            _logger.warning("Bitbucket api token required! (-p / --api-token)")
            sys.exit(1)
        try:
            api_token = getpass(prompt="Enter your bitbucket API Token: ")
        except KeyboardInterrupt, SystemExit:
            return
    if not location:
        if non_interactive:
            _logger.warning("local location required (-l / --location)")
            sys.exit(1)
        try:
            location = input("Enter local location to backup to: ")
        except KeyboardInterrupt, SystemExit:
            return
    location = os.path.abspath(location)

    # ok to proceed
    try:
        repos = get_repositories(
                username=username,
                user_email=user_email,
                api_token=api_token,
                team=team,
                )
        repos = sorted(repos, key=lambda repo_: repo_.get("name"))
        dir_list = []
        if not repos:
            _logger.error("No repositories found. Are you sure you provided the correct password")

        if repos and len(repos) == 0:
            _logger.error("No repositories found.")

        for repo in repos:
            dir_list.append(repo.get("slug"))
            if repo.get("has_wiki"):
                dir_list.append(repo.get("slug") + "_wiki")

            if args.ignore_repo_list and repo.get("slug") in args.ignore_repo_list:
                _logger.info(f"ignoring repo {repo.get('name')} with slug: {repo.get('slug')}")
                continue

            if (
                repo_whitelist
                and len(repo_whitelist) != 0
                and repo.get("slug") not in repo_whitelist
            ):
                _logger.info(f"ignoring repo {repo.get('name')} with slug: {repo.get('slug')}")
                continue

            _logger.info(f"Backing up [{repo.get('name')}]...")
            backup_dir = os.path.join(location, repo.get("slug"))

            for attempt in range(1, max_attempts + 1):
                try:
                    if not os.path.isdir(backup_dir):
                        clone_repo(
                            repo,
                            backup_dir,
                            http,
                            api_token,
                            mirror=_mirror,
                            with_wiki=_with_wiki,
                            fetch_lfs=_fetchlfs,
                        )
                    else:
                        _logger.info(f"Repository [{repo.get('name')}] already in place, just updating...")
                        update_repo(
                            repo,
                            backup_dir,
                            http,
                            api_token,
                            with_wiki=_with_wiki,
                            prune=args.prune,
                            fetch_lfs=_fetchlfs,
                        )
                except KeyboardInterrupt:
                    raise KeyboardInterrupt
                #except SystemExit:
                #    raise SystemExit
                except SystemExit:
                    if attempt == max_attempts:
                        raise MaxBackupAttemptsReached(
                            "repo [%s] is reached maximum number [%d] of backup tries"
                            % (repo.get("name"), attempt)
                        )
                    _logger.warning(f"Failed to backup repository [{repo.get('name')}], keep trying, {max_attempts - attempt} attempts remain")
                except:
                    traceback.print_exc()
                    break
                else:
                    break

        if args.compress:
            if not sys.platform.startswith('win'):
                _logger.warning("compress not available on windows")
            else:
                compress(repo, location)
        _logger.info("Finished!")
    except (KeyboardInterrupt):
        _logger.warning("Operation cancelled. There might be inconsistent data in location directory.")
        sys.exit(1)
    except SystemExit:
        _logger.warning("Exit Requested, There might be inconsistent data in location directory.")
        sys.exit(1)
    except MaxBackupAttemptsReached as e:
        _logger.warning(f'Unable to backup: {e}')
        sys.exit(1)
    except:
        _logger.error(traceback.format_exc())
        sys.exit(1)


if __name__ == "__main__":
    main()
