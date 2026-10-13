# libmp-mirror

[![Checks](https://github.com/Ewanophobia/libmp-mirror/actions/workflows/check.yml/badge.svg)](https://github.com/Ewanophobia/libmp-mirror/actions/workflows/check.yml)
[![Sync and publish](https://github.com/Ewanophobia/libmp-mirror/actions/workflows/sync.yml/badge.svg)](https://github.com/Ewanophobia/libmp-mirror/actions/workflows/sync.yml)

A public, open source Wally package for [Roblox LibMP](https://github.com/Roblox/libmp), the Luau library for reading Roblox MicroProfiler data. Anyone can install and use this package. This is an unofficial distribution maintained by Ewanophobia; Roblox develops LibMP.

## Install

Add this to your project's `wally.toml`:

```toml
[dependencies]
LibMP = "ewanophobia/libmp-mirror@1.1.0"
```

Run `wally install`. If your project does not already map `Packages` with Rojo, add this under your project's `ReplicatedStorage` tree:

```json
"Packages": {
  "$path": "Packages"
}
```

Then require it from either a client or server script:

```luau
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local LibMP = require(ReplicatedStorage.Packages.LibMP)

print("Profiler ready:", LibMP.Control:IsBackendReady())
```

The package has no Wally dependencies. Its Rojo project maps the package root directly to the unmodified `libmp.luau` ModuleScript, so the dependency alias is directly requireable. See Roblox's [quick start](https://github.com/Roblox/libmp#quick-start) and [API reference](https://github.com/Roblox/libmp/blob/main/docs/api-reference.md) for profiling and capture examples.

To get a newly published version within your version constraint, run `wally update`. Wally lockfiles keep existing projects on their installed version until updated. Update the constraint when adopting a new major version.

## Automatic updates

[Sync and publish](https://github.com/Ewanophobia/libmp-mirror/actions/workflows/sync.yml) checks upstream every hour, at minute 17, and can also be run manually from GitHub Actions. GitHub may delay scheduled jobs. No computer needs to stay running.

The workflow:

1. Finds the highest numbered, non-draft, non-prerelease GitHub release. It accepts `1.1`, `1.1.0`, and `v1.1.0` style tags; upstream's unversioned rolling `latest` release is ignored.
2. Downloads the released Luau library and ZIP when available, checks GitHub's SHA-256 asset digests, and verifies the direct module matches the ZIP. ZIP extraction reads only the library and license notices. If licenses are absent from the ZIP, it reads them from the exact release tag's commit.
3. Commits the unmodified module, notices, Wally version, and [`upstream.json`](upstream.json) provenance to this repository.
4. Builds the package, publishes it to the public Wally registry, verifies the downloadable registry contents, and creates a matching GitHub release with the package ZIP.

Upstream `1.1` becomes Wally `1.1.0`. Normally package versions match upstream. If upstream replaces a release asset or its notices, the mirror increments its patch version, since published Wally versions cannot be replaced. If that uses a version later selected by upstream, the mirror increments again to keep versions increasing; `upstream.json` always records the exact source release. Prereleases and development-branch commits are not published.

Publishing is retried even when no new commit is needed, so a temporary registry failure can recover on the next run. Existing registry packages must match the local package contents. An occasional empty keepalive commit prevents GitHub from disabling scheduled workflows after 60 days without repository activity.

### Maintainers

The repository's encrypted Actions secret `WALLY_AUTH_TOKEN` contains the token from `wally login` for the GitHub account that owns the `ewanophobia` Wally scope. A normal Actions `GITHUB_TOKEN` cannot publish to Wally. If authorization is revoked, log in to Wally again and replace the secret; never commit or paste the token into a workflow.

Local checks require Python 3.11+, Wally 0.3.2, and Rojo:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/sync_upstream.py
mkdir -p dist
wally package --list
wally package --output dist/libmp-mirror.zip
rojo build default.project.json --output dist/libmp-mirror.rbxm
```

The sync script does not publish. Publishing uses `scripts/publish.py --archive dist/libmp-mirror.zip` with `WALLY_AUTH_TOKEN` in the environment. CI pins Actions and downloaded tools and checks their archive hashes.

## License and attribution

LibMP and this mirror's packaging code are available under the [MIT license](LICENSE). The upstream file remains byte-for-byte unchanged, including its attribution header. Its embedded dependencies retain their respective licenses:

- [FlatBuffers: Apache-2.0](LICENSES/flatbuffers.txt)
- [LZ4: BSD-2-Clause](LICENSES/lz4.txt)
- [Spider runtime: MPL-2.0](LICENSES/spider_runtime.txt)

The bundled notices and upstream module header include source references. See [Roblox's dependency information](https://github.com/Roblox/libmp/tree/main/src/dependencies). Those licenses apply to their respective components; the Wally manifest lists all included licenses.
