# --strip-auth, --auth and --auth-file

!!! warning

    This flag is only used for basic auth.

By default `conda-lock` will leave basic auth credentials for private conda channels in the lock file (unless you make use of environment variables for your passwords).
If you wish to strip authentication from the file, provide the `--strip-auth` argument.

```bash
conda-lock --strip-auth --file environment.yml
```

In order to `conda-lock install` a lock file with its basic auth credentials stripped, you will need to create an authentication file in `.json` format like this:

```json
{
  "domain": "username:password"
}
```

If you have multiple channels that require different authentication within the same domain, you can additionally specify the channel like this:

```json
{
  "domain.org/channel1": "username1:password1",
  "domain.org/channel2": "username2:password2"
}
```

You can provide the authentication either as string through `--auth` or as a filepath through `--auth-file`.

```bash
conda-lock install --auth-file auth.json conda-linux-64.lock
```
