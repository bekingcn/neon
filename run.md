
```SH
sudo apt update

sudo apt install build-essential libtool libreadline-dev zlib1g-dev flex bison libseccomp-dev \
libssl-dev clang pkg-config libpq-dev cmake postgresql-client \
libcurl4-openssl-dev openssl lsof libicu-dev
```

# python3-poetry**
# sudo apt install python3-poetry 
pipx install poetry

# protobuf-compiler**
curl -fsSL "https://github.com/protocolbuffers/protobuf/releases/download/v25.1/protoc-25.1-linux-$(uname -m | sed 's/aarch64/aarch_64/g').zip" -o "protoc.zip" \
    && unzip -q protoc.zip -d protoc \
    && sudo mv protoc/bin/protoc /usr/local/bin/protoc \
    && sudo mv protoc/include/google /usr/local/include/google \
    && rm -rf protoc.zip protoc
```

```SH
# recommended approach from https://www.rust-lang.org/tools/install
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

source "$HOME/.cargo/env"

```


```SH
# Note: The path to the neon sources can not contain a space.

# git clone --recursive https://github.com/neondatabase/neon.git
# cd neon

# git submodules - postgres 14,15,16
git submodule update --init --recursive --depth 2 --progress

# The preferred and default is to make a debug build. This will create a
# demonstrably slower build than a release build. For a release build,
# use "BUILD_TYPE=release make -j`nproc` -s"
# Remove -s for the verbose build log

# 

make -j`nproc` -s
```


**1. Start ...**
```SH
# Create repository in .neon with proper paths to binaries and data
# Later that would be responsibility of a package install script
cargo neon init


# start pageserver, safekeeper, and broker for their intercommunication
cargo neon start


# create initial tenant and use it as a default for every future neon_local invocation
cargo neon tenant create --set-default


# create postgres compute node
cargo neon endpoint create main


# start postgres compute node
cargo neon endpoint start main


# check list of running postgres instances
cargo neon endpoint list
```

**2. Connect with PSQL**
```SH
psql -p55432 -h 127.0.0.1 -U cloud_admin postgres
```

```SQL
CREATE TABLE t(key int primary key, value text);

insert into t values(1,1);

select * from t;
```

**3. Branches use case**
```SH
# create branch named migration_check
cargo neon timeline branch --branch-name migration_check


# check branches tree
cargo neon timeline list


# create postgres on that branch
cargo neon endpoint create migration_check --branch-name migration_check


# start postgres on that branch
cargo neon endpoint start migration_check


# check the new list of running postgres instances
cargo neon endpoint list


# this new postgres instance will have all the data from 'main' postgres,
# but all modifications would not affect data in original postgres
psql -p55434 -h 127.0.0.1 -U cloud_admin postgres

# SQL
select * from t;
insert into t values(2,2);
select * from t;


# check that the new change doesn't affect the 'main' postgres
psql -p55432 -h 127.0.0.1 -U cloud_admin postgres
```


**4. Stop**
```SH
cargo neon stop
```


**Run tests**
```SH
# git clone --recursive https://github.com/neondatabase/neon.git

CARGO_BUILD_FLAGS="--features=testing" make

./scripts/pytest
```

**Other - aws cli**
```SH
curl "https://awscli.amazonaws.com/awscli-exe-linux-$(uname -m).zip" -o "awscliv2.zip" \
    && unzip -q awscliv2.zip \
    && ./aws/install \
    && rm awscliv2.zip
```