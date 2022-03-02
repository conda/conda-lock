# Authentication for channels

Conda lock supports two kinds of credentials used for channels
## Token based

These are used by [anaconda.org](https://anaconda.org/), [Anaconda Enterprise](https://www.anaconda.com/products/enterprise),
[Anaconda Team Edition](https://www.anaconda.com/products/team) and [Quetz](https://github.com/mamba-org/quetz).

These should be specified making of the **environment variable form**

!!! note "Specifying"

    === "environment.yml"

        ```yaml
        channels:
            - http://host.com/t/$MY_REPO_TOKEN/channel
        ```

    === "meta.yaml"

        ```yaml
        extra:
            channels:
                - http://host.com/t/$MY_REPO_TOKEN/channel
        ```

    === "pyproject.toml"

        ```toml
        [tool.conda-lock]
        channels = [
            'http://host.com/t/$MY_REPO_TOKEN/channel'
        ]
        ```

    === "shell arguments"

        Make sure this environment variable is **not** expanded (quote types matter).

        ```sh
        --channel 'http://host.com/t/$MY_REPO_TOKEN/channel'
        ```

        If you accidentally pass a channel url that contains a token and its gets expanded like in this case

        ```sh
        --channel "http://host.com/t/$MY_REPO_TOKEN/channel"
        ```

        conda lock will attempt detect the environment variable used, preferring that the environment variables with
        a sensible suffix (`KEY`, `TOKEN`, `PASS`, etc).

The _name_ of the environment variable(s) will form part of your lock and you will have to have that SAME
environment variable set if you wish to run the install.

```sh
# retrieve secrets from some store
source $(./get-lockfile-env-vars-from-secret-store)
# use the secrets as part of the conda-lock invocation
conda-lock install -n my-env-with-a-secret conda-lock.yml
```

## Simple Auth

For other channels (such as those self-managed) basic auth is supported and has the same environment variable
behavior as for token based channel urls.

```sh
--channel 'http://$USER:$PASSWORD@host.com/channel'
```

Additionally simple auth also support the [--strip-auth, --auth and --auth-file](/flags#-strip-auth-auth-and-auth-file) flags.

## What gets stored

Since we can generally assume that these substitutions are both volatile _and_ secret `conda-lock` will not store
the raw version of a url in the unified lockfile.

If it encounters a channel url that looks as if it contains a credential portion it will search the currently
available environment variables for a match with that variable.  In the case of a match that portion of the url
will be replaced with a environment variable.

??? example "Example output in unified lockfile"

    ```yaml
    metadata:
    channels:
    - url: https://host.tld/t/$QUETZ_API_KEY/channel_name
        used_env_vars:
        - QUETZ_API_KEY
    package:
    - platform: linux-64
    url: https://host.tld/t/$QUETZ_API_KEY/channel_name/linux-64/libsomethingprivate-22.02.00.tar.bz2
    version: 22.02.00
    ```

The rendered lockfiles will contain substituted environment variables, so if you are making use of `conda-lock`
in conjunction with git these should _NOT_ be checked into version control.

[anaconda.org]: https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html#create-env-file-manually