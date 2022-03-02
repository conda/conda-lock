# Authentication for channels

Conda lock supports two kinds of credentials used for channels

## Token based

These are used by [anaconda.org](https://anaconda.org/), [Anaconda Enterprise](https://www.anaconda.com/products/enterprise),
[Anaconda Team Edition](https://www.anaconda.com/products/team) and [Quetz](https://github.com/mamba-org/quetz).

To pass one of these channels specify them in your source with an environment variable

Make sure this environment variable is not expanded (quote types matter).

```sh
--channel 'http://host.com/t/$MY_REPO_TOKEN/channel'
```

If you accidentally pass a channel url that contains a token like so

```sh
--channel "http://host.com/t/$MY_REPO_TOKEN/channel"
```

Then conda lock will detect the environment variabl used (provided that the environment variable used ends in a sensible suffix (KEY, TOKEN, PASS, etc)).

The _name_ of the environment variable will form part of your lock and you will have to have that SAME environment variable set if you wish to run the install

```sh
# retrieve secrets from some store
source $(./get-lockfile-env-vars-from-secret-store)
# use the secrets as part of the conda-lock invocation
conda-lock install -n my-env-with-a-secret conda-lock.yml
```

## Simple Auth

For other channels (such as those self-managed) basic auth is supported

```sh
--channel 'http://$USER:$PASSWORD@host.com/channel'
```

This can also be done using the following flags

{%
   include-markdown "./flags/strip-auth.md"
   heading-offset=2
%}

## What gets stored

Since we can generally assume that these substitutions are both volatile *and* secret `conda-lock` will not store
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

The rendered lockfiles will contain substituted environment variables so if you are making use of `conda-lock`
in conjunction with git these should *NOT* be checked into version control.



[anaconda.org]: https://docs.conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html#create-env-file-manually