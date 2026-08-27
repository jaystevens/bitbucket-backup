#!/usr/bin/env python
import argparse
import datetime
import os
import subprocess
import sys
import re
import base64
import time
import traceback
from getpass import getpass

import requests
from requests.auth import HTTPBasicAuth

try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote

try:
    input = raw_input
except NameError:
    pass

try:
    _range = xrange
except NameError:
    _range = range

_verbose = False
_quiet = False


class MaxBackupAttemptsReached(Exception):
    pass


def debug(message, output_no_verbose=False):
    """
    Outputs a message to stdout taking into account the options verbose/quiet.
    """
    global _quiet, _verbose
    if not _quiet and (output_no_verbose or _verbose):
        print("%s - %s" % (datetime.datetime.now(), message))


def exit(message, code=1):
    """
    Forces script termination using C based error codes.
    By default, it uses error 1 (EPERM - Operation not permitted)
    """
    global _quiet
    if not _quiet and message and len(message) > 0:
        sys.stderr.write("%s (%s)\n" % (message, code))
    sys.exit(code)


def exec_cmd(command, stop_on_error=True):
    """
    Executes an external command taking into account errors and logging.
    """
    global _verbose
    # Mask the token value while keeping username structure
    command_masked = re.sub(r"(x-bitbucket-api-token-auth:)[^@]+", r"\1*****", command)
    command_masked = re.sub(r"(Authorization:\s*Basic\s+)[^\"]+", r"\1*****", command_masked)
    debug("Executing command: %s" % command_masked)
    if not _verbose:
        if "nt" == os.name:
            command = "%s > nul 2> nul" % command
        else:
            command = "%s > /dev/null 2>&1" % command
    resp = subprocess.call(command, shell=True)
    if resp != 0:
        if stop_on_error:
            exit("Command [%s] failed" % command_masked, resp)
        else:
            debug("Command [%s] failed: %s" % (command_masked, resp))
    return resp


def compress(repo, location):
    """
    Creates a TAR.GZ file with all contents cloned by this script.
    """
    os.chdir(location)
    debug("Compressing repositories in [%s]..." % location, True)
    exec_cmd(
        "tar -zcvf bitbucket-backup-%s-%s.tar.gz `ls -d *`"
        % (
            repo.get("owner").get("username") or repo.get("owner").get("nickname"),
            datetime.datetime.now().strftime("%Y%m%d%H%m%s"),
        )
    )
    debug("Cleaning up...", True)
    for d in os.listdir(location):
        path = os.path.join(location, d)
        if os.path.isdir(path):
            exec_cmd("rm -rfv %s" % path)

def build_api_token_header(api_token: str) -> str:
    if api_token is None:
        debug("build_api_auth_header - api_token is None!")
        return ''
    raw_auth = f"x-bitbucket-api-token-auth:{api_token}"
    b64_auth = base64.b64encode(raw_auth.encode("utf-8")).decode("utf-8")
    auth_header = f"Authorization: Basic {b64_auth}"
    # use with: '-c "http.extraHeader={auth_header}"'
    # or with : '-c "http.https://bitbucket.org/.extraHeader={auth_header}"'
    return auth_header

def clean_api_creds_from_repo(backup_dir: str) -> None:
    debug('Cleaning repo of saved credentials')
    # clear saved HTTP creds from REPO
    exec_cmd(f'git -C "{backup_dir}" config --local --unset-all http.extraHeader', stop_on_error=False)
    exec_cmd(f'git -C "{backup_dir}" config --local --unset-all http.https://bitbucket.org/.extraHeader', stop_on_error=False)


def fetch_lfs_content(backup_dir: str, api_token: str = None, http: bool = False):
    debug("Fetching LFS content...")
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
                debug('error Fetching LFS Content... Giving Up')
                break
            else:
                debug(f'error Fetching LFS Content... Retry attempt {retry_counter}')
                time.sleep(1)


def get_repositories(
        username: str = None,
        api_token: str = None,
        team: str = None
):
    auth = None
    repos = []
    try:
        if all((username, api_token)):
            auth = HTTPBasicAuth(username, api_token)
        if auth is None:
            exit("Must provide username/api_token")
        if not team or username:
            response = requests.get("https://api.bitbucket.org/2.0/user/", auth=auth)
            if response.status_code == 401:
                exit("Unauthorized! Check your credentials and try again.", 22 )
            username = response.json().get("username")
        url = "https://api.bitbucket.org/2.0/repositories/{}/".format(team or username)

        debug("Fetching Repository List")
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
            exit(
                "Unauthorized! Check your credentials and try again.", 22
            )  # EINVAL - Invalid argument
        else:
            exit(
                "Connection Error! Bitbucket returned HTTP error [%s]."
                % e.response.status_code
            )
    debug(f"Found {len(repos)} Repositories")
    return repos


def clone_repo(
    repo,
    backup_dir,
    http,
    username,
    api_token,
    mirror=False,
    with_wiki=False,
    fetch_lfs=False,
):
    global _quiet, _verbose
    slug = repo.get("slug")
    #owner = repo.get("owner").get("username") or repo.get("owner").get("nickname")
    owner = repo.get("workspace").get("slug") or repo.get("owner").get("username")
    owner_url = quote(owner, safe="@")
    
    if http and not all((username, api_token)):
        exit("Cannot backup via http without username and api_token")
    
    slug_url = quote(slug)
    command = None

    git_command = "git clone"
    if mirror:
        git_command = "git clone --mirror"
    if http:
        #git_command = f'{git_command} -c "http.extraHeader={build_api_token_header(api_token)}"'
        git_command = f'{git_command} -c "http.https://bitbucket.org/.extraHeader={build_api_token_header(api_token)}"'

        command = "%s https://bitbucket.org/%s/%s.git" % (
            git_command,
            owner_url,
            slug_url,
        )
    else:
        command = "%s git@bitbucket.org:%s/%s.git" % (
            git_command,
            owner_url,
            slug_url,
        )
        
    if not command:
        exit("could not build command")

    debug("Cloning %s..." % repo.get("name"))
    exec_cmd('%s "%s"' % (command, backup_dir))

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
        debug("Cloning %s's Wiki..." % repo.get("name"))
        exec_cmd("%s/wiki %s_wiki" % (command, backup_dir))



def update_repo(
        repo,
        backup_dir,
        http,
        api_token,
        with_wiki=False,
        prune=False,
        fetch_lfs=False
):
    command = None
    os.chdir(backup_dir)

    command = "git remote update"

    if http:
        raw_auth = f"x-bitbucket-api-token-auth:{api_token}"
        b64_auth = base64.b64encode(raw_auth.encode("utf-8")).decode("utf-8")
        auth_header = f"Authorization: Basic {b64_auth}"
        command = f'git -c "http.extraHeader={auth_header}" remote update'

    if prune:
        command = "%s %s" % (command, "--prune")

    if not command:
        exit("could not build command")

    debug("Updating %s..." % repo.get("name"))
    exec_cmd(command)

    if fetch_lfs:
        fetch_lfs_content(backup_dir, api_token, http)

    wiki_dir = "%s_wiki" % backup_dir
    if with_wiki and repo.get("has_wiki") and os.path.isdir(wiki_dir):
        os.chdir(wiki_dir)
        debug("Updating %s's Wiki..." % repo.get("name"))
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
    parser.add_argument("-u", "--username", dest="username", help="Bitbucket account email address")
    parser.add_argument("-p", "--api-token", dest="api_token", help="Bitbucket API Token")
    parser.add_argument("-t", "--team", dest="team", help="Bitbucket team")
    parser.add_argument(
        "-l", "--location", dest="location", help="Local backup location"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        dest="verbose",
        help="Verbose output of all cloning commands",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", dest="quiet", help="No output to stdout"
    )
    parser.add_argument(
        "-c",
        "--compress",
        action="store_true",
        dest="compress",
        help="Creates a compressed file with all cloned repositories (cleans up location directory)",
    )
    parser.add_argument(
        "-a",
        "--attempts",
        dest="attempts",
        type=int,
        default=1,
        help="max. number of attempts to backup repository",
    )
    parser.add_argument(
        "--mirror",
        action="store_true",
        help="Clone just bare repositories with git clone --mirror (git only)",
    )
    parser.add_argument(
        "--fetchlfs",
        action="store_true",
        help="Fetch LFS content after clone/pull (git only)",
    )
    parser.add_argument(
        "--with-wiki", dest="with_wiki", action="store_true", help="Includes wiki"
    )
    parser.add_argument(
        "--http", action="store_true", help="Fetch via https instead of SSH"
    )
    parser.add_argument(
        "--skip-password",
        dest="skip_password",
        action="store_true",
        help="Ignores password prompting if no password is provided (for public repositories)",
    )
    parser.add_argument(
        "--prune", dest="prune", action="store_true", help="Prune repo on remote update"
    )
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
    location = args.location
    username = args.username
    api_token = args.api_token
    repo_whitelist = args.repo_whitelist
    http = args.http
    max_attempts = args.attempts
    global _quiet
    _quiet = args.quiet
    global _verbose
    _verbose = args.verbose
    _mirror = args.mirror
    _fetchlfs = args.fetchlfs
    _with_wiki = args.with_wiki
    if _quiet:
        _verbose = False  # override in case both are selected
    team = args.team

    if not username:
        username = input("Enter bitbucket username email: ")
    if not api_token:
        api_token = getpass(prompt="Enter your bitbucket API Token: ")
    if not location:
        location = input("Enter local location to backup to: ")
    location = os.path.abspath(location)

    # ok to proceed
    try:
        repos = get_repositories(
                username=username,
                api_token=api_token,
                team=team,
                )
        repos = sorted(repos, key=lambda repo_: repo_.get("name"))
        dir_list = []
        if not repos:
            print(
                "No repositories found. Are you sure you provided the correct password"
            )
        for repo in repos:
            dir_list.append(repo.get("slug"))
            if repo.get("has_wiki"):
                dir_list.append(repo.get("slug") + "_wiki")

            if args.ignore_repo_list and repo.get("slug") in args.ignore_repo_list:
                debug(
                    "ignoring repo %s with slug: %s"
                    % (repo.get("name"), repo.get("slug"))
                )
                continue

            if (
                repo_whitelist
                and len(repo_whitelist) != 0
                and repo.get("slug") not in repo_whitelist
            ):
                debug(
                    "ignoring repo %s with slug: %s"
                    % (repo.get("name"), repo.get("slug"))
                )
                continue

            debug("Backing up [%s]..." % repo.get("name"), True)
            backup_dir = os.path.join(location, repo.get("slug"))

            for attempt in range(1, max_attempts + 1):
                try:
                    if not os.path.isdir(backup_dir):
                        clone_repo(
                            repo,
                            backup_dir,
                            http,
                            username,
                            api_token,
                            mirror=_mirror,
                            with_wiki=_with_wiki,
                            fetch_lfs=_fetchlfs,
                        )
                    else:
                        debug(
                            "Repository [%s] already in place, just updating..."
                            % repo.get("name")
                        )
                        update_repo(
                            repo,
                            backup_dir,
                            http,
                            api_token,
                            with_wiki=_with_wiki,
                            prune=args.prune,
                            fetch_lfs=_fetchlfs,
                        )
                except:
                    traceback.print_exc()
                    if attempt == max_attempts:
                        raise MaxBackupAttemptsReached(
                            "repo [%s] is reached maximum number [%d] of backup tries"
                            % (repo.get("name"), attempt)
                        )
                    debug(
                        "Failed to backup repository [%s], keep trying, %d attempts remain"
                        % (repo.get("name"), max_attempts - attempt)
                    )
                else:
                    break

        if args.compress:
            compress(repo, location)
        debug("Finished!", True)
    except (KeyboardInterrupt, SystemExit):
        exit(
            "Operation cancelled. There might be inconsistent data in location directory.",
            0,
        )
    except MaxBackupAttemptsReached as e:
        exit("Unable to backup: %s" % e)
    except:
        if not _quiet:
            traceback.print_exc()
        exit("Unknown error.", 11)  # EAGAIN - Try again


if __name__ == "__main__":
    main()
