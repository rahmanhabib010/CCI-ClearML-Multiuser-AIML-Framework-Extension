# ClearML Authentik Backend patch

This package patches ONLY the backend source tree. Keep the corresponding source ZIP/version unchanged before applying.

```bash
bash apply.sh --check /path/to/backend-repo
bash apply.sh --apply /path/to/backend-repo
bash apply.sh --rollback /path/to/backend-repo
```

Checks fail closed if the source differs or the patch is already applied. Apply both frontend and backend packages for the intended SSO flow, then configure Authentik and rebuild/deploy per the main README. Backups are stored in this patch folder.

**Caution:** These are the previously prepared experimental patches, split for independent installation. Live OIDC login and builds have not been verified.
