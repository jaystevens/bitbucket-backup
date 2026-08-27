# Bitbucket backup

## Description
This python script will backup all of your Bitbucket repos locally.  
If the repository does not exist locally the repo will be cloned to the <local_backup_location>.  
If the repo does exist locally an `git remote update` will be run.  


## Quickstart
```bash
bitbucket-backup [-u / --username <username>] [-e / --email <bitbucket_email>] [-p / --api-token <api_token>]
  [-l <local_backup_location>] [-t <bitbucket_team>] [-a] [-v] [-q] [-c] [--http] [--skip-password] [--mirror]
  [--prune] [--fetchlfs]
```
You can backup a team's repositories instead of your own by supplying the optional `-t` parameter
and entering the team slug (this is now called a "Workspace" by BitBucket).

## API Tokens (jaystevens Fork)
API Tokens are the replacement for App Passwords, that are the replacement for using Passwords.  
Username (Bitbucket Email) and API Token are needed to access the Bitbucket API to get a repo listing.  
Clone/Update/LFS will use API Tokens when `--http` is specified, otherwise it should use SSH Keys.  
The API Token must have read repositories permission.  
<https://support.atlassian.com/bitbucket-cloud/docs/api-tokens/>  

# Creating API Tokens:
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
- add support for reading API Token from environment variable 'BITBUCKET_API_TOKEN'
    - this can prevent the api token from being in your shell history or visible to `ps`
    - FYI this spawns git commands with the base64 auth header when connecting over HTTPS