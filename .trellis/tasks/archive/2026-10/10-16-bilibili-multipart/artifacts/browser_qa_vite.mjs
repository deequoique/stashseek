// Disposable configuration; production and the user's demo are untouched.
import { fileURLToPath } from "node:url";
const webRoot = fileURLToPath(new URL("../../../../web", import.meta.url));
export default {
  root: webRoot,
  server: { host: "127.0.0.1", port: 5181, strictPort: true,
    proxy: { "/api": "http://127.0.0.1:8021" } },
};
