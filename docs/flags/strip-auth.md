# --strip-auth, --auth and --auth-file

!!! warning

    This flag is only used for basic auth.

By default `conda-lock` will leave basic auth credentials for private conda channels _in the manner in which they were specified_.

This means that if you should specified your channel as

!!! success "Non-leaky credentials"

    ```{.yaml title="environment.yml"}
    channels:
        - http://$CHANNEL_USER:$CHANNEL_PASSWORD@host.com/channel
    ```

    !!! note ""

        The environment variables `CHANNEL_USER` and `CHANNEL_PASSWORD` will be required at install time.

!!! fail "Leaky credentials"

    ```{.yaml title="environment.yml"}
    channels:
        - http://username:password123@host.com/channel
    ```

When used with explicit/env render targets you may wish to strip the basic auth from these files (regardless of if it is correctly or incorrectly specified).

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

You can provide the authentication either as a yaml/json string through `--auth`

```bash
conda-lock install --auth "{domain: 'username:$PASSWORD'}" conda-linux-64.lock
```

or as a filepath through  `--auth-file`.

```bash
conda-lock install --auth-file auth.json conda-linux-64.lock
```
