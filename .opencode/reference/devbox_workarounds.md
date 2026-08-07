# Devbox Workarounds

`cr.flyte.org/flyteorg/flyte-devbox` is the local Flyte v2 dev cluster (k3s + flyte-binary + rustfs + knative + docker-registry inside one Docker container). Most behavior matches a production Flyte deploy, but several quirks bite us on every deploy iteration. This file is the running ledger of those quirks and how to step around them.

When you hit a deploy/runtime issue against devbox that takes more than one round-trip to diagnose, append it here with a one-line description and the minimum the next session needs to know.

**Automation:** the *cluster-side* workarounds below (signed-URL endpoint, serving domain off `.localhost`, CoreDNS wildcard, and the restarts that race the addon controller) are applied to a fresh devbox by [`cli/devbox-setup.sh`](../../cli/devbox-setup.sh) — run it once after recreating the container (`./cli/devbox-setup.sh`, or `--dry-run` to preview, `--laptop` to also apply the macOS DNS steps, `--domain` to override). It's idempotent. The remaining entries are app-code/design (already in the codebase), not scriptable; keep this file and the script in sync when you add a new cluster-side quirk.

---

## Storage signed URLs use `localhost`

**Symptom:** Inside any App pod, `flyte.serve.aio(env)` (or any code-bundle upload) fails with `All connection attempts failed` on `http://localhost:30002/flyte-data/...`.

**Cause:** `flyte-binary-config` has `signedURL.endpoint: http://localhost:30002`. The control plane returns signed upload URLs with that host. Pods see `localhost` as their own loopback — nothing is listening there.

There is no single host/IP that's reachable from both the laptop and from in-cluster pods on macOS Docker Desktop (node IPs aren't routable from the host VM, `localhost` is the pod loopback). The fix targets pods (production parity) and pays a dev-machine cost on the laptop.

**Workaround:**

1. Patch the source manifest inside the devbox container (the `flyte-binary-config` configmap is owned by a k3s Addon controller, so `kubectl patch` reverts):

   ```bash
   docker exec flyte-devbox sed -i \
     's|endpoint: http://localhost:30002|endpoint: http://rustfs-svc.flyte:9000|' \
     /var/lib/rancher/k3s/server/manifests/flyte.yaml
   kubectl rollout restart deployment/flyte-binary -n flyte
   ```

   Pods now resolve `rustfs-svc.flyte:9000` via k8s DNS — same code path as a production Flyte that returns real S3 URLs.

2. On the laptop, make `rustfs-svc.flyte:9000` resolvable:
   - DNS / `/etc/hosts`: `rustfs-svc.flyte → 127.0.0.1`
   - Port-forward: `kubectl port-forward -n flyte svc/rustfs-svc 9000:9000`

   `app/admin_app.py:main()` starts the port-forward automatically (`_start_storage_port_forward()`) so `python -m app.admin_app` "just works" given the DNS step is done once.

---

## `AppEnvironment(secrets=[...])` is silently dropped

**Symptom:** Secret registered with `flyte create secret`, declared on the `AppEnvironment` via `secrets=[flyte.Secret(...)]`, never reaches the running container. `os.environ["MY_SECRET"]` raises `KeyError`.

**Cause:** `secrets=[...]` is dropped at the flyte-binary → Knative translation — **not** merely the missing label. Verified by deploying an AppEnvironment with `secrets=[flyte.Secret(...)]` and inspecting the rendered ksvc: `kubectl get ksvc <app> -n flyte -o jsonpath='{.spec.template.metadata.annotations}'` shows only `autoscaling.knative.dev/*` — no secret annotations and no `inject-flyte-secrets` label. The `flyte-binary-webhook` (`failurePolicy: Fail`, `objectSelector matchLabels: inject-flyte-secrets=true`) injects from pod *annotations*, so with neither annotation nor label present **no cluster-side webhook change can rescue it** — there is nothing for the webhook to act on. This is a Flyte App-serving limitation, reproducible on any cluster, not a pure devbox quirk.

**Workaround:** Bake secret values into `env_vars={...}` from the deployer's local shell at deploy time. Example in `app/admin_app.py`:

```python
_SECRET_NAMES = ("GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET", "SESSION_SECRET")
_GITHUB_APP_NAMES = ("GITHUB_APP_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_SLUG")
_STORAGE_NAMES = ("PINATA_JWT",)
_RUNTIME_SECRETS = {
    name: os.environ[name]
    for name in (*_SECRET_NAMES, *_GITHUB_APP_NAMES, *_STORAGE_NAMES)
    if os.environ.get(name)
}
app_env = flyte.app.AppEnvironment(..., env_vars=_RUNTIME_SECRETS)
```

**The deployer's shell is the only source of these — export all of them before `python -m app.admin_app`.** Keep this list in sync with `app/admin_app.py`; a var that isn't exported is silently omitted, so the pod starts fine and misbehaves later:

| Env var | Needed for | If missing |
| --- | --- | --- |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | OAuth login | login fails outright (loud) |
| `SESSION_SECRET` | session cookie + proxy cookie check | no session survives (loud) |
| `GITHUB_APP_ID` **and** `GITHUB_APP_PRIVATE_KEY` | signing the App JWT that mints fork-scoped tokens | **silent** — the login-time install check raises, gets swallowed as "app not installed", and Workspace saving reads *disabled* for every user who genuinely has a fork + install |
| `GITHUB_APP_SLUG` | the App install-redirect URL | `/workspace/enable` skips the install step |
| `PINATA_JWT` | `/assets` routes | asset manager renders "not configured" |

The two App credentials are **all-or-nothing**: the id is useless without the key. Neither set is a valid pre-App deploy; only *some* set is a deploy mistake, so `app/admin_app.py` logs a `Partial GitHub App config` warning at import for exactly that case. This bit us once already — `GITHUB_APP_PRIVATE_KEY` became required in `916dbdc` (2026-06-05, plan 18, when `workspace_enabled` moved from `fork + OAuth token` to `fork + live App-install check`) and went unexported for two months, because the install callback trusts the install and only a *fresh login* re-checks it.

**Trade-off / prod gap:** secret values are stored in the App spec in Flyte's DB. This is the one accepted parity gap in `app/` — revisit when Flyte supports App-pod secret injection (then switch to `secrets=[flyte.Secret(key=…, as_env_var=…)]` and drop the baking).

Paths investigated and rejected: (a) `pod_template=PodTemplate(labels={"inject-flyte-secrets": "true"})` — even with the label the webhook has no secret annotations to inject, because flyte-binary never stamps them on App pods; (b) relaxing the webhook `objectSelector` — same reason, and `failurePolicy: Fail` makes a match-all selector dangerous (a webhook blip would block all pod scheduling).


**Symptom:** App pod crash-loops before user code runs, logs show `GenericError: Generic S3 error ... http://rustfs.flyte:9000/... Name or service not known`.

**Cause:** `/var/lib/rancher/k3s/server/manifests/flyte.yaml` (line ~7776) sets `internalApps.defaultEnvVars.FLYTE_AWS_ENDPOINT = http://rustfs.flyte:9000`. The actual k8s service is `rustfs-svc`. (The `plugins.k8s.default-env-vars` block uses the correct name; only `internalApps` is wrong.) The manifest is owned by a k3s Addon controller, so `kubectl patch` on the ConfigMap reverts.

**Workaround:** Patch the manifest inside the devbox container (lost on container restart):

```bash
docker exec flyte-devbox sed -i \
  's|FLYTE_AWS_ENDPOINT: http://rustfs.flyte:9000|FLYTE_AWS_ENDPOINT: http://rustfs-svc.flyte:9000|' \
  /var/lib/rancher/k3s/server/manifests/flyte.yaml
docker exec flyte-devbox kubectl rollout restart deployment/flyte-binary -n flyte
```

**The restart races the addon controller — you usually need to restart twice.** Editing the manifest file triggers the k3s Addon controller to re-render the `flyte-binary-config` ConfigMap, but that's async. A `rollout restart` issued right after the `sed` will often boot a flyte-binary pod that mounts the *old* ConfigMap, and flyte-binary reads config only once at startup — so freshly-deployed App pods still get `FLYTE_AWS_ENDPOINT: http://rustfs.flyte:9000` even though the manifest file is patched. The deploy fails identically and looks like the patch didn't take.

Verify the live ConfigMap (not the manifest file) reflects the change, *then* restart again:

```bash
# confirm the ConfigMap the controller actually serves is patched
docker exec flyte-devbox kubectl get cm flyte-binary-config -n flyte -o yaml | grep -nE 'rustfs.*:9000'
# all hits should read rustfs-svc.flyte; then restart so flyte-binary loads it
docker exec flyte-devbox kubectl rollout restart deployment/flyte-binary -n flyte
docker exec flyte-devbox kubectl rollout status deployment/flyte-binary -n flyte --timeout=120s
```

To diagnose: check the actual env on a failed App pod — `kubectl get pod <pod> -n flyte -o yaml | grep -A1 FLYTE_AWS_ENDPOINT`. If it shows the bare `rustfs.flyte` while the ConfigMap shows `rustfs-svc.flyte`, flyte-binary is running stale config; restart it again. Delete the failed ksvc (`kubectl delete ksvc admin-app-flytesnacks-development -n flyte`) before redeploying so you get a clean revision.

---

## App pod needs `flyte.init_in_cluster()`, not `flyte.init()`

**Symptom:** `Client has not been initialized` from the first SDK call inside an App pod, even though FastAPI's lifespan called `flyte.init()`.

**Cause:** `fserve` spawns the configured `args` (e.g. uvicorn) as a `Popen(..., env=os.environ, shell=True)` subprocess. The subprocess inherits env vars but not Python process state, so the parent fserve's client init is lost. `flyte.init()` with no args does not auto-discover from env vars; `flyte.init_in_cluster()` does (reads `_U_EP_OVERRIDE`, `_U_INSECURE`, `EAGER_API_KEY`, `FLYTE_INTERNAL_EXECUTION_PROJECT/DOMAIN`, `_U_ORG_NAME`).

**Workaround:** In the App's own startup hook, branch on `_U_EP_OVERRIDE` and call `flyte.init_in_cluster(project=..., domain=...)`. Pass `project` explicitly — `with_servecontext(project=...)` does not propagate to the code-bundle upload client. See `app/init.py`.

---

## `flyte create project` (and any CLI subprocess) fails inside App pods

**Symptom:** Shelling out to `flyte create project ...` (or any `flyte` CLI command) from inside a running App pod raises `InitializationError` / `Client has not been initialized` immediately, even though the same pod's in-process Python SDK works fine.

**Cause:** Same root as the previous entry — the pod's Flyte connection is auto-discovered from `_U_EP_OVERRIDE` and friends at *Python process startup* by `flyte.init_in_cluster()`. A fresh subprocess inherits those env vars but does not run the discovery logic before its first SDK call, so `ensure_client()` raises. The Flyte v2 docs at `core-concepts/projects-and-domains` further claim that "the Python SDK provides read-only access to projects, to create or modify projects use the `flyte` CLI or the UI" — this is **wrong against the installed SDK**, `flyte.remote.Project.create(...)` exists and is what the CLI itself calls under the hood.

**Workaround:** When provisioning Flyte resources from inside an App pod, always prefer the in-process SDK (`Project.create.aio(...)`, `Project.get.aio(...)`, etc.) over CLI subprocesses. The pod's auto-discovered endpoint is only available to the parent Python process. Trust the SDK's actual surface over the v2 docs when they disagree. See `app/provision.py` for the working pattern.

---

## Node has ~8 GiB allocatable memory

**Symptom:** OOM-killed pods when concurrent tasks exceed combined memory budget.

**Cause:** The single-node k3s cluster inside the devbox container has **~7.75 GiB allocatable**.

**Workaround:** Keep `outer-coordinator + concurrent children ≤ ~7.5 GiB`. The scRNA pipeline runs sequentially so a single child at `memory=("2Gi", "6Gi")` fits. Parallel fan-outs need smaller per-child limits.

---

## `App.url` is the console URL, not the public URL

**Symptom:** Browser redirected to `http://flyte-binary-http.flyte:8090/v2/...` after a successful `flyte.serve()`; "site can't be reached / DNS_PROBE" because the hostname is in-cluster only.

**Cause:** `flyte.remote.App.url` is documented as "the console URL for viewing the app" (i.e. the Flyte Console deep-link) — not the user-facing endpoint. The user-facing URL lives on `App.endpoint` (`status.ingress.public_url`). The SDK's own log line `"Deployed App ..., you can check the console at {deployed.url}"` is the giveaway. Not devbox-specific but easy to miss.

**Workaround:** Use `app.endpoint` whenever you mean "the URL a browser should hit." Reserve `app.url` for linking into the Flyte console.

---

## Serving domain must not be `.localhost` (so `App.endpoint` resolves in-cluster)

**Symptom:** A pod (e.g. the admin app) calling another App's `App.endpoint` over HTTP fails with `[Errno -2] Name or service not known` / `[Errno -5] No address associated with hostname`. Example: notebook Save (`POST /workspace/save` → notebook pod's `/__sg__/workspace/sync`) returned `could not reach notebook: ...`. (The admin App is Knative-scaled-to-zero between requests, so the failing call runs in a freshly-activated *pod*, not on the laptop — easy to misdiagnose as a local issue.)

**Cause:** Stock devbox serves apps under the `localhost` TLD, so `App.endpoint` is `http://{ksvc}.localhost:30081`. Two compounding problems: (1) cluster DNS doesn't know `*.localhost`; (2) more fundamentally, **glibc special-cases the `.localhost` TLD and never sends it to a nameserver at all** (`getaddrinfo('x.localhost')` → EAI_NODATA / EAI_NONAME without a single DNS query), so *no* CoreDNS change can fix `.localhost`. The fix is to move apps off `.localhost` onto a normal domain (here `devbox.stargazer.bio` — needs no real public DNS records) and make that domain resolve in-cluster. Then app code uses `App.endpoint` everywhere with **zero devbox branches**.

**Two hostnames must agree.** kourier routes by the request `Host`, so the Knative *route* host and Flyte's *`App.endpoint`* host must be the same domain or you get a 404 / no-route:
- **Knative `config-domain`** (`knative-serving` ns) sets the ksvc route host → `{ksvc}.{domain}`.
- **Flyte `internalApps.baseDomain`** (in `flyte-binary-config`'s `100-inline-config.yaml`) sets `App.endpoint` → `http://{ksvc}.{baseDomain}:{ingressAppsPort}`. `App.endpoint` is computed live from this — flyte-binary restart picks it up, **no app redeploy needed**. `ingressAppsPort: 30081` is the kourier NodePort (kept as-is so the laptop entrypoint is unchanged).

**Workaround (ad-hoc devbox steps, no app code).** Both domain values live in the k3s addon manifest, so patch the source (`kubectl edit` on the live ConfigMaps reverts):

```bash
# 1a. Knative route domain
docker exec flyte-devbox sed -i 's|^  localhost: ""|  devbox.stargazer.bio: ""|' \
  /var/lib/rancher/k3s/server/manifests/flyte.yaml
# 1b. Flyte App.endpoint domain
docker exec flyte-devbox sed -i 's|baseDomain: localhost|baseDomain: devbox.stargazer.bio|' \
  /var/lib/rancher/k3s/server/manifests/flyte.yaml
```

The addon controller re-applies within ~10s (it races — poll the live ConfigMaps before restarting):
`kubectl get cm config-domain -n knative-serving -o jsonpath='{.data}'` → `{"devbox.stargazer.bio":""}` and `kubectl get cm flyte-binary-config -n flyte -o yaml | grep baseDomain` → `devbox.stargazer.bio`. Then `kubectl rollout restart deployment/flyte-binary -n flyte` (Knative reconciles ksvc route URLs on its own).

2. Make `*.{domain}` resolve **to the node IP** inside the cluster (so the `:30081` NodePort is reachable from pods) via a `coredns-custom` server block. k3s CoreDNS imports `/etc/coredns/custom/*.server`. The node IP is hardcoded (re-apply on cluster recreate); fetch it at apply time:

```bash
NODEIP=$(docker exec flyte-devbox kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
docker exec -i flyte-devbox kubectl apply -f - <<YAML
apiVersion: v1
kind: ConfigMap
metadata: { name: coredns-custom, namespace: kube-system }
data:
  devbox-domain.server: |
    devbox.stargazer.bio:53 {
        template IN A {
            match .*\.devbox\.stargazer\.bio\.$
            answer "{{ .Name }} 60 IN A $NODEIP"
        }
        template IN AAAA {
            match .*\.devbox\.stargazer\.bio\.$
            rcode NOERROR
        }
    }
YAML

**CoreDNS template syntax is line-based — do not collapse to one line.** Each
`match`/`answer`/`rcode` directive must be on its own line inside the
`template … { }` block. The one-line `{ match … ; answer … }` form is invalid
Corefile syntax (`;` isn't a separator there) — CoreDNS rejects it with
`plugin/template: … Wrong argument count or unexpected line ending after 'template'`
and **CrashLoopBackOff**s, which takes cluster DNS down and cascades into
`flyte-binary` failing to start. (`cli/devbox-setup.sh` generates the
multi-line form.)
docker exec flyte-devbox kubectl rollout restart deployment/coredns -n kube-system
```

Why node IP and not the kourier ClusterIP: `App.endpoint` carries `:30081`, which is a NodePort — only reachable on a node IP, not on a ClusterIP (which listens on 80). kourier still routes by the public `Host`, so resolving to the node IP and hitting `:30081` works. The AAAA template returns NOERROR-empty so glibc (AF_UNSPEC) falls back to the A record cleanly.

**Verify** from a throwaway pod (`kubectl run t --image=localhost:30000/notebook-app:latest --command -- sleep 200`):
`getaddrinfo('{ksvc}.devbox.stargazer.bio', 30081)` → node IP, and `GET http://{ksvc}.devbox.stargazer.bio:30081/health` → 200.

**Laptop side:** the browser already reached `*.localhost:30081` via 127.0.0.1 + the published `:30081` docker port; only the hostname changes. Point `*.devbox.stargazer.bio` → `127.0.0.1` with a wildcard resolver (the published `:30081` port is unchanged). No real public DNS records are needed — CoreDNS handles pods, the local resolver handles the laptop:

```bash
brew install dnsmasq
echo 'address=/devbox.stargazer.bio/127.0.0.1' >> $(brew --prefix)/etc/dnsmasq.conf
sudo brew services start dnsmasq
sudo mkdir -p /etc/resolver
printf 'nameserver 127.0.0.1\n' | sudo tee /etc/resolver/devbox.stargazer.bio
# verify: scutil --dns | grep -A1 devbox.stargazer.bio ; ping -c1 anything.devbox.stargazer.bio  -> 127.0.0.1
```

---

## Auth cookies are non-`Secure` on devbox (http), `Secure` in prod (TLS)

**Symptom (if mis-defaulted):** With `Secure` cookies forced on, login over devbox's plain HTTP silently fails — the browser never sends a `Secure` cookie over http, so every request looks unauthenticated and you bounce back to the login page.

**Cause:** Devbox serves the admin and per-notebook apps over `http://…:30081` (no TLS). A `Secure` cookie is dropped by the browser on http, so it can never round-trip.

**Workaround / design:** The `Secure` attribute is **parametrized**, not hardcoded — `app.config.SECURE_COOKIES` (the app-tier config home) parses `STARGAZER_SECURE_COOKIES` (truthy = `1/true/yes/on`), defaulting **off** for devbox/http. In production behind TLS, `export STARGAZER_SECURE_COOKIES=1` before `python -m app.admin_app`; it's baked into the admin App env (`_PUBLIC_CONFIG`, re-serialized from `config.SECURE_COOKIES`) and propagated into each notebook pod's env (`per_notebook_env`), so the standalone proxy's mirror (`sg_proxy._cookie_secure`) — which can't import `app.config` — sets the cookie identically. `httponly=True` and `samesite="lax"` stay constant — only `Secure` is environment-dependent. All cookie writes go through `admin_app._session_redirect` (session) / the proxy middleware (launch handoff); there is no other set-cookie site to keep in sync.

---

## Flyte's code bundle ships only `.py` files

Not strictly devbox-specific but bites on every devbox deploy.

**Symptom:** Non-Python assets (HTML templates, YAML configs) missing at runtime in the deployed pod even though `pyproject.toml` lists them as `package-data`. The Flyte bundle on `/home/flyte/` shadows the installed copy on `sys.path`, so `package-data` doesn't help.

**Workaround:** Add `include=("dir/",)` (relative to the file where the env is instantiated) to the `AppEnvironment` / `TaskEnvironment`. Prefer `Path(__file__).parent / "asset_dir"` over `importlib.resources.files("pkg")` for asset lookup.

Related Python-side gotcha: modules imported only inside function bodies are excluded from Flyte's static-analysis code bundle. Make them statically reachable (e.g. import in the package `__init__.py`) or add to `include`.

---

## Flyte's code bundle shadows image-baked top-level packages

Not strictly devbox-specific. Bites any AppEnvironment whose image bakes a Python module under a package name that the deployer's process also imports from.

**Symptom:** `uvicorn <pkg>.<mod>:asgi_app` fails inside the pod with `ERROR: Error loading ASGI app. Could not import module "<pkg>.<mod>"`, even though `<pkg>/<mod>.py` is verifiably present at the image-baked path (e.g. `/usr/local/lib/app/proxy.py`) and `PYTHONPATH` includes that directory.

**Cause:** Flyte's `loaded_modules` bundler ships every `.py` file imported by the deploying Python process into the pod's WORKDIR (`/home/flyte`). If that bundle includes a `<pkg>/__init__.py` (because the deployer imports `<pkg>.something_else`), the unpacked `/home/flyte/<pkg>/` shadows the image-baked `/usr/local/lib/<pkg>/` on `sys.path` — Python's cwd entry (`''`) comes before `PYTHONPATH`. The cwd version doesn't contain the baked module that wasn't separately imported by the deployer, so the import fails.

**Workaround:** Bake the runtime-only file as a TOP-LEVEL module under a name the deployer doesn't import. E.g. `/usr/local/lib/sg_proxy.py` (not `/usr/local/lib/app/proxy.py`); reference it as `uvicorn sg_proxy:asgi_app`. The top-level slot is free; only package directories collide.
