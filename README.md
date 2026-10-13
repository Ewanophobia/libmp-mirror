# libmp-mirror

My Wally mirror of [Roblox's LibMP](https://github.com/Roblox/libmp). The `libmp.luau` file is copied directly from upstream releases without changes.

## Install

Add to your `wally.toml`:

```toml
[dependencies]
LibMP = "ewanophobia/libmp-mirror@1.1.1"
```

Run `wally install`, then require it:

```luau
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local LibMP = require(ReplicatedStorage.Packages.LibMP)
```

This assumes your Rojo project maps `Packages` into `ReplicatedStorage`. Works on both the client and server, with no Wally dependencies.

See the upstream [quick start](https://github.com/Roblox/libmp#quick-start) and [API reference](https://github.com/Roblox/libmp/blob/main/docs/api-reference.md) for usage.

## Updates

A [GitHub Action](https://github.com/Ewanophobia/libmp-mirror/actions/workflows/sync.yml) checks hourly for new numbered stable releases and publishes them here and on Wally. Prereleases and the rolling `latest` tag are skipped.

Versions follow upstream (`1.1` becomes `1.1.0`), with extra patch releases for packaging changes or replaced upstream files. [upstream.json](upstream.json) records the source release and checksums. Run `wally update` to update an existing install.

## License

The scripts, configuration, and documentation in this repo are under the [Unlicense](LICENSE).

LibMP keeps Roblox's [MIT license](LICENSES/libmp.txt). Its bundled dependencies keep their own licenses: [FlatBuffers](LICENSES/flatbuffers.txt), [LZ4](LICENSES/lz4.txt), and [Spider runtime](LICENSES/spider_runtime.txt).
