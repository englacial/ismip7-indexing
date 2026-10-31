// Read the local dev Icechunk repo (served over HTTP) with icechunk-js + zarrita,
// resolving one virtual chunk from the public source.coop bucket.
// Usage: node read_with_icechunk_js.mjs http://127.0.0.1:8765/icechunk-ais CESM2-WACCM/historical/SDBN1-8000m acabf
import { IcechunkStore, Repository, HttpStorage } from "icechunk-js";
import * as zarr from "zarrita";

const [repoUrl, groupPath, varname] = process.argv.slice(2);
const t0 = Date.now();
const repo = await Repository.open({ storage: new HttpStorage(repoUrl) });
const session = await repo.checkoutBranch("main");
console.log("snapshot", session.getSnapshotId(), "message", JSON.stringify(session.getMessage()), `(${Date.now() - t0} ms)`);
const store = await IcechunkStore.open(session);
console.log("nodes:", store.listNodes().map((n) => n.path).join(", "));
const arr = await zarr.open(store.resolve(`/${groupPath}/${varname}`), { kind: "array" });
console.log("array", varname, "shape", arr.shape, "chunks", arr.chunks, "dtype", arr.dtype, "attrs", JSON.stringify(arr.attrs));
const meta = store.getMetadata(`/${groupPath}/${varname}`);
console.log("fill_value", meta.fill_value);
const t1 = Date.now();
const chunk = await zarr.get(arr, [0, null, null]); // first timestep, virtual chunk on S3
const data = chunk.data;
let finite = 0, fill = 0, min = Infinity, max = -Infinity;
for (const v of data) { if (v > 1e30) fill++; else if (Number.isFinite(v)) { finite++; if (v < min) min = v; if (v > max) max = v; } }
console.log(`timestep 0: ${data.length} values, ${finite} finite, ${fill} fill, min ${min.toExponential(3)}, max ${max.toExponential(3)} (${Date.now() - t1} ms)`);
const time = await zarr.open(store.resolve(`/${groupPath}/time`), { kind: "array" });
const tv = await zarr.get(time);
console.log("time attrs", JSON.stringify(time.attrs), "first/last", tv.data[0], tv.data[tv.data.length - 1]);
const crs = await zarr.open(store.resolve(`/${groupPath}/crs`), { kind: "array" });
console.log("crs grid_mapping_name", crs.attrs.grid_mapping_name, "lat0", crs.attrs.latitude_of_projection_origin);
