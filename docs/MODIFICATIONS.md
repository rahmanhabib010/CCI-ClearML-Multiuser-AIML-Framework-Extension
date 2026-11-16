# Modifications to Upstream ClearML for the CCI xG Testbed

This document describes CCI-specific modifications and integration work built upon the original [ClearML Server](https://github.com/clearml/clearml-server) and [ClearML Web](https://github.com/clearml/clearml-web). The inventory draws on implementation notes, code excerpts, applied patches, configurations, and reported tests; it is not a file-by-file diff against a fixed upstream commit or an independent security audit.

## 1\. Attribution and Scope

**Inherited upstream capabilities:** ClearML experiment tracking, datasets/artifacts, task queues, API and File Servers, Python SDK, Agent execution, and their core Web UI. **Pre-existing infrastructure:** Authentik, CILogon/VT identity, and OpenStack. **CCI-specific work:** Integration and modifications for OIDC SSO, JIT provisioning, identity linkage, logout, project visibility/authorization, WebApp experience, HTTPS routing, and deployment workflow.

Evidence labels: **Code** indicates a shared source excerpt or identified file; **Observed** indicates reported command or browser behavior; **Deployment** indicates a described integration or configuration. Section 12 summarizes the reported implementation status.

## 2\. Backend Authentication and OIDC Integration

**Upstream:** ClearML provides its own authentication and API services, without the CCI-specific Authentik provider configuration.

**CCI modifications (Code; Observed):**

* Added `clearml-server/apiserver/sso\\\_authentik.py` with OIDC configuration/start/callback handlers such as `/sso/config`, `/sso/start`, and `/sso/callback`.
* Registered the authentication blueprint through `apiserver/server.py` and adjusted `apiserver/server\\\_init/request\\\_handlers.py` to admit the custom routes.
* Implemented an OIDC authorization-code flow with PKCE S256, state/nonce handling, provider discovery, and token verification; the shared source also referenced issuer/audience and verified-email checks.
* Issued a ClearML application token through existing authentication-business-logic methods, with browser cookie handling.
* Updated the Angular login flow to discover SSO availability and initiate Authentik redirection.
* Observed successful Authentik/CILogon-to-dashboard login after configuring the callback and claims.

## 3\. Just-in-Time User Provisioning and Identity Mapping

**Upstream:** ClearML already has user-creation and authentication APIs, but not this specific CCI Authentik provisioning path.

**CCI modifications (Code; Observed):**

* Added JIT provisioning for newly authenticated, authorized identities rather than relying solely on a preapproved email list.
* Reused the existing `AuthBLL.create\\\_user(...)` mechanism to create ClearML user records with a regular user role.
* Added `apiserver/sso\\\_identity.py` to persist an identity link based on trusted OIDC issuer and subject.
* Parameterized default ClearML company and token lifetime through SSO configuration variables.
* Applied the Authentik JIT patch and observed successful import checks and reported new-user dashboard access.

## 4\. Single Logout and Local Logout

**Upstream:** ClearML implements token-based sessions and has existing token-revocation-related authentication logic.

**CCI modifications (Code; partially Observed):**

* Added SLO-related backend routes including `/sso/backchannel-logout` and `/sso/session-status`.
* Added `apiserver/sso\\\_logout\\\_store.py` for logout/session tracking and integration with existing server revocation facilities.
* Extended frontend logout/session monitoring, including reported edits to user effects and the main page.
* Added a distinct ClearML-only logout path: ending the local ClearML session does not necessarily sign the person out of the existing Authentik account.
* Updated request-handler configuration to allow the custom logout routes.

## 5\. Project Visibility Frontend

**Upstream:** ClearML Web includes project cards, menus, dialogs, and project management.

**CCI modifications (Code; Observed):**

* Added a **Visibility** action to the project-card context menu and an eye-outline icon.
* Implemented a visibility dialog/workflow using existing Angular event/output and Material dialog patterns.
* Modified or worked within `project-card-menu`, `projects-list`, `common-projects-page`, and `project-dialog` component paths.
* Confirmed that the customized visibility menu and eye icon appeared and functioned in the WebApp.

## 6\. Project-Level Visibility and Authorization Backend

**Upstream:** ClearML project services do not provide this CCI-specific public/private visibility policy and owner/admin mutation guard.

**CCI modifications (Code; Observed):**

* Customized `apiserver/services/projects.py` to set a private default for new projects in the shown service logic.
* Added project visibility selection and validation through a custom `projects.set\\\_visibility` behavior.
* Added visibility-aware filtering in selected project lookups, including `projects.get\\\_by\\\_id`.
* Applied a write-guard patch to `projects.update`, `projects.move`, `projects.merge`, and `projects.delete`. The patch command reported successful installation.
* Intended permission model: other ordinary users may view permitted public content but cannot perform protected owner operations; the owner and authorized administrator retain applicable management capabilities.

## 7\. SDK Credentials and Researcher Integration

**Upstream:** The ClearML Python SDK, `clearml-init`, and authenticated task/dataset APIs already exist.

**CCI integration (Deployment):**

* Configured `/etc/caddy/Caddyfile`, `clearml-web/proxy.config.mjs`, and `clearml-server/docker/docker-compose.yml` to expose the Web, API, and File endpoints under the testbed HTTPS hostname.
* Customized `clearml-server/apiserver/services/auth.py` to implement role-aware API credential controls for CCI xG Testbed researchers, administrators, and GPU-based ClearML Agents.
* Extended `clearml-server/apiserver/services/auth.py` so authorized administrators can provision and manage API access/secret key pairs for designated users and GPU agents while restricting ordinary users from independently creating, modifying, or revoking those credentials.

## 8\. DNS, HTTPS, Caddy, and WebApp Routing

**Upstream:** ClearML exposes separate Web, API, and File services; generic reverse-proxy deployment is not itself new functionality.

**CCI integration (Deployment; Observed):**

* Configured the public ClearML hostname `https://clearml.ccixgtestbed.org` and browser-trusted HTTPS entry through Caddy on port `443`.
* Routed the customized Angular development UI on port `4300`, API service on `8008`, and File Server on `8081` through the configured proxy.
* Routed relevant `/api`, `/files`, and `/sso` paths and aligned OIDC callback/launch URLs with the public origin.
* Used `clearml-web/proxy.config.mjs`, `clearml-web/angular.json`, and `clearml-server/docker/docker-compose.yml` for relevant development/deployment settings.
* Reported HTTPS access and a successful `/sso/config` result after DNS and callback configuration changes.

## 9\. Backend Data and Session Services

**Upstream:** ClearML's server deployment uses MongoDB, Redis, and Elasticsearch alongside its API/File/Web services.

**CCI integration (Code; Deployment):**

* Retained MongoDB-backed users/projects/task metadata and existing application database aliases.
* Reused existing Redis-based token revocation behavior where referenced by customized logout/session handling.
* Retained File Server handling of dataset and model artifacts.
* Continued to rely on backend services present in the ClearML Docker deployment.

Redis and Elasticsearch remain part of the deployment inventory even when they are omitted from a simplified architecture figure.

## 10\. CCI OpenStack CPU/GPU Execution Integration

**Upstream:** ClearML Agent already supports polling queues, preparing environments, and executing remote jobs.

**CCI integration (Deployment):**

* Separated management services on an OpenStack CPU VM from GPU-intensive tasks on remote GPU-capable OpenStack VMs.
* Integrated the experiment-management API and task queues with GPU-side ClearML Agent workers.
* Designed the workflow for SDK experiment registration, explicit task enqueueing, Agent execution, and centralized result monitoring.
* Prepared a synthetic RAN slice dataset and LSTM demonstration workflow as an integration example.

Remote execution requires an explicitly queued task and a connected worker; merely running a local training script does not demonstrate GPU-queue execution.

## 11\. Key Files and Configuration Touchpoints

|Component|Reported file or area|Purpose|
|-|-|-|
|Backend|`apiserver/sso\\\_authentik.py`|OIDC routes and validation|
|Backend|`apiserver/sso\\\_identity.py`|Persistent identity linkage|
|Backend|`apiserver/sso\\\_logout\\\_store.py`|Customized logout/session handling|
|Backend|`apiserver/server.py`|SSO route registration|
|Backend|`apiserver/server\\\_init/request\\\_handlers.py`|Custom route allowlisting|
|Backend|`apiserver/services/projects.py`|Visibility and project authorization|
|Frontend|`src/app/webapp-common/login/login/login.component.ts`|SSO login experience|
|Frontend|`src/app/webapp-common/core/effects/users.effects.ts`|Session/logout effects|
|Frontend|Project card/list/dialog components|Visibility interface and events|
|Frontend|`src/index.html`|Reported SLO/session-monitor integration|
|Deployment|`clearml-server/docker/docker-compose.yml`|Backend deployment and SSO environment references|
|Development|`clearml-web/proxy.config.mjs`, `angular.json`|API/SSO routing and allowed Web host|
|Host-only config|`/etc/caddy/Caddyfile`|HTTPS reverse proxy; not included in code trees|
|Existing identity system|Authentik provider/application configuration|OIDC client and claims mapping; not ClearML source|

File locations reflect the shared implementation history; inspect the current combined repository to establish an exact modified-file list. Never commit `.env`, credentials, private keys, or production token values.

## 12\. Implementation Status

**Reported operational:** The integrated ClearML service is currently deployed and operational for authorized CCI xG Testbed users and is undergoing active testing to identify bugs, address issues, and continuously improve its functionality, reliability, and user experience. Reported observations include Authentik-to-ClearML browser login, successful JIT import checks, the visibility menu and eye icon, application of the project write-guard patch, and availability of the SSO configuration endpoint over public HTTPS.

## 13\. Upstream Acknowledgment

The original ClearML Server, ClearML WebApp, SDK, File Server, queueing, and Agent capabilities were provided by [ClearML](https://github.com/clearml) and its contributors. Authentik, CILogon, and OpenStack are pre-existing systems. This document attributes only the testbed-specific adaptation, customization, and integration to the CCI development effort.

