# Bitbucket backup

## Description
This python script will backup all of your Bitbucket repos locally.  
If the repository does not exist locally the repo will be cloned to the <local_backup_location>.  
If the repo does exist locally an `git remote update` will be run.  


## Quickstart
```bash
bitbucket-backup [-u / --username <username>] [-e / --email <bitbucket_email>] [-p / --api-token <api_token>]
  [-l <local_backup_location>] [-t <bitbucket_team>] [-a / --attempts] [--http] [--mirror] [--fetchlfs] [-v] [-c / --compress (only available on unix)]  [--skip-password] 
  [--prune] 
```

You can backup a team's repositories instead of your own by supplying the optional `-t` parameter
and entering the team slug (this is now called a "Workspace" by BitBucket).  

example mirror command:  ```bitbucket_backup.py -u <username> -e <email> -p <API_TOKEN> -l backup_dir --http --mirror --fetchlfs```

# options:
- `-u` / `--username` is optional, if omitted the api token requires `read:user:bitbucket` permission.
- `-e` / `--email` is required.
- `-p` / `--api-token` / environment variable `BITBUCKET_API_TOKEN` is required.
- `-l` / `--location` is required.
- all other options are optional.



## API Tokens
API Tokens are the replacement for App Passwords, that are the replacement for using Passwords.  
Bitbucket Email and API Token are needed to access the Bitbucket API to get the repo listing.  
Clone/Update/LFS will use API Tokens when `--http` is specified, otherwise it should use SSH Keys.  

# Creating API Tokens:
- <https://support.atlassian.com/bitbucket-cloud/docs/api-tokens/>
- <https://id.atlassian.com/manage-profile/security/api-tokens>
- `Create API token with scopes`
- Name API token: add a 'name' and expire date
- Select the app: `Bitbucket`
- Select Bitbucket scopes:
    - `read:user:bitbucket` only required for username lookup if `-u` / `--username` not specified
    - `read:repository:bitbucket` for repo access


## jaystevens Fork Changes
- removed HG support
- remove OAUTH support (it is painful to use in something like this)
- remove password / App Password support
- added API Token support (Bitbucket's replacement for App Passwords)
- use API Tokens when connecting over HTTPS (`--http` option)
- updated to not print out API Token in logging.
- updated to clean a repo remote URL and config to not save the API Token.
- SSH Keys are not tested by me, they may or may not work.
- tested repo listing using API Tokens
- tested Clone/Update/Fetch-LFS with API Tokens
- changed attempts to 3 (from 1)
- added retry to FetchLFS (3 attempts, attempts for FetchLFS are hard coded)
- add support for reading API Token from environment variable 'BITBUCKET_API_TOKEN'
    - this can prevent the api token from being in your shell history or visible to `ps`
    - FYI this spawns git commands with the base64 auth header when connecting over HTTPS and will be visiable to `ps`