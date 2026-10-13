# libmp-mirror

My Wally mirror of [Roblox's LibMP](https://github.com/Roblox/libmp). The `libmp.luau` file is copied directly from upstream releases without changes.

## Install

Add to your `wally.toml`:

```toml
[dependencies]
LibMP = "ewanophobia/libmp-mirror@=1.1.0"
```

Run `wally install`, then require it:

```luau
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local LibMP = require(ReplicatedStorage.Packages.LibMP)
```

This assumes your Rojo project maps `Packages` into `ReplicatedStorage`. Works on both the client and server, with no Wally dependencies.

See the upstream [quick start](https://github.com/Roblox/libmp#quick-start) and [API reference](https://github.com/Roblox/libmp/blob/main/docs/api-reference.md) for usage.

## Updates

A [GitHub Action](https://github.com/Ewanophobia/libmp-mirror/actions/workflows/sync.yml) checks hourly for new numbered stable releases, updates this repo, and publishes to Wally. Prereleases and the rolling `latest` tag are skipped.

Versions follow upstream exactly (`1.1` becomes `1.1.0` for Wally). Packaging changes don't get their own version. [upstream.json](upstream.json) records the source release and checksums.

The `=` pins the version: an earlier packaging-only `1.1.1` is still in Wally, which doesn't let package owners delete versions. To upgrade when Roblox releases a new version, change the pin and run `wally update`. If upstream changes a file without a new version, the sync stops rather than overwriting a published package.

## License

The scripts, configuration, and documentation in this repo are under the [Unlicense](LICENSE).

LibMP keeps Roblox's [MIT license](LICENSES/libmp.txt). Its bundled dependencies keep their own licenses: [FlatBuffers](LICENSES/flatbuffers.txt), [LZ4](LICENSES/lz4.txt), and [Spider runtime](LICENSES/spider_runtime.txt).
