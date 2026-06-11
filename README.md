# toolbox-bridge — RETIRED (rewritten in Rust)

This Python implementation is **deprecated and removed**. Toolbox-Bridge is now
a pure Rust tool living in the umbrella repo's cargo workspace:

**https://github.com/tom2025b/linux-ops-suite — `crates/toolbox-bridge`**

The rewrite also changed the architecture: the bridge no longer invokes
`bulwark scan` or writes `.scriptvault.yaml` files directly. It reads Bulwark's
findings from the compiled **Workstate snapshot** and publishes ScriptVault
sidecar metadata as a versioned **Workstate feed**
(`contracts/toolbox-bridge.workstate-feed.v1.schema.json`). No direct
tool-to-tool communication.

Install via the suite installer:

```bash
cd ~/projects/linux-ops-suite && ./install.sh --force --only toolbox-bridge
```

The Python source remains in this repo's git history (last version: tag the
commit before this one) if it is ever needed for reference.
