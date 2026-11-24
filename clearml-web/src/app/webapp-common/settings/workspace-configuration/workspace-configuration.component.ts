import {Component, inject, OnInit, signal} from '@angular/core';
import {selectCurrentUser} from '@common/core/reducers/users-reducer';
import {Store} from '@ngrx/store';
import {ApiAuthService} from '~/business-logic/api-services/auth.service';

type CredentialTab = 'python' | 'jupyter';
interface PersonalCredential {access_key: string; secret_key: string; label: string}
interface CredentialSummary {access_key: string; label: string; last_used: string | null}

@Component({
  selector: 'sm-workspace-configuration',
  templateUrl: './workspace-configuration.component.html',
  styleUrls: ['./workspace-configuration.component.scss'],
  standalone: false,
  styles: [`
    .self-service-card { margin: 22px 0; padding: 22px; border: 1px solid #d9dfe9; border-radius: 8px; max-width: 900px; }
    .self-service-card h3 { margin: 0 0 8px; }
    .self-service-card button, .self-service-dialog button { cursor: pointer; padding: 9px 14px; border-radius: 5px; }
    .self-service-card button:disabled, .self-service-dialog button:disabled { cursor: default; opacity: .5; }
    .self-service-dialog-overlay { position: fixed; inset: 0; background: rgba(0,0,0,.45); display: grid; place-items: center; z-index: 2000; }
    .self-service-dialog { position: relative; background: white; padding: 24px; border-radius: 12px; width: min(740px, calc(100vw - 40px)); max-height: 85vh; overflow-y: auto; }
    .self-service-tabs { display: flex; gap: 12px; margin: 20px 0; }
    .self-service-tabs button[aria-selected="true"] { border-bottom: 2px solid #0074c8; font-weight: bold; }
    .self-service-config { background: #f1f4fb; padding: 16px; overflow-x: auto; white-space: pre; font: 12px/1.6 monospace; }
    .self-service-notice { margin-top: 12px; color: #475569; }
    .self-service-error { color: #b91c1c; margin: 12px 0; }
    .self-service-close { float: right; }
    .self-service-table { width: 100%; border-collapse: collapse; margin-top: 16px; }
    .self-service-table th, .self-service-table td { padding: 10px 8px; border-bottom: 1px solid #d9dfe9; text-align: left; }
    .self-service-table th { font-weight: 600; }
    .self-service-table code { word-break: break-all; }
    .self-service-revoke { color: #af2020; }
    .self-service-hint { color: #586579; font-size: 12px; }
    .self-service-actions { display: flex; gap: 10px; align-items: center; margin: 12px 0; }
  `]
})
export class WorkspaceConfigurationComponent implements OnInit {
  private store = inject(Store);
  private authApi = inject(ApiAuthService);
  protected currentUser = this.store.selectSignal(selectCurrentUser);
  protected canManageApiCredentials = signal(false);
  protected eligible = signal(false);
  protected existingCredentials = signal(false);
  protected requesting = signal(false);
  protected requestError = signal('');
  protected shownCredentials = signal<PersonalCredential | null>(null);
  protected selectedTab = signal<CredentialTab>('python');
  protected myCredentials = signal<CredentialSummary[]>([]);
  protected revokingKey = signal('');

  ngOnInit(): void {
    this.authApi.authCredentialAdminStatus().subscribe({
      next: result => this.canManageApiCredentials.set(result?.can_manage_credentials === true),
      error: () => this.canManageApiCredentials.set(false)
    });
    this.authApi.authSelfServiceCredentialStatus().subscribe({
      next: result => {
        this.eligible.set(result?.eligible === true);
        this.existingCredentials.set(result?.has_credentials === true);
        if (result?.eligible === true) { this.loadMyCredentials(); }
      },
      error: () => this.eligible.set(false)
    });
  }

  protected requestMyCredentials(): void {
    if (!this.eligible() || this.requesting() || this.existingCredentials()) {return;}
    this.requesting.set(true);
    this.requestError.set('');
    this.authApi.authRequestMyCredentials().subscribe({
      next: result => {
        this.requesting.set(false);
        if (!result?.credentials?.access_key || !result?.credentials?.secret_key) {
          this.requestError.set('The server did not return both credential values.');
          return;
        }
        this.shownCredentials.set(result.credentials);
        this.existingCredentials.set(true);
        this.loadMyCredentials();
        this.selectedTab.set('python');
      },
      error: () => {
        this.requesting.set(false);
        this.requestError.set('Unable to issue credentials. If you already have credentials, contact the administrator.');
      }
    });
  }

  protected loadMyCredentials(): void {
    if (!this.eligible()) {return;}
    this.authApi.authMyCredentials().subscribe({
      next: result => {
        const credentials = result?.credentials || [];
        this.myCredentials.set(credentials);
        this.existingCredentials.set(credentials.length > 0);
      },
      error: () => this.requestError.set('Unable to load your API credential list.')
    });
  }

  protected revokeMyCredential(accessKey: string): void {
    if (!this.eligible() || this.revokingKey() || !accessKey) {return;}
    if (!window.confirm('Revoke this API key? Applications using it will immediately lose access.')) {return;}
    this.revokingKey.set(accessKey);
    this.requestError.set('');
    this.authApi.authRevokeMyCredentials(accessKey).subscribe({
      next: () => {
        this.revokingKey.set('');
        this.loadMyCredentials();
      },
      error: () => {
        this.revokingKey.set('');
        this.requestError.set('Unable to revoke your API key.');
      }
    });
  }

  protected setTab(tab: CredentialTab): void { this.selectedTab.set(tab); }

  protected configurationText(): string {
    const c = this.shownCredentials();
    if (!c) { return ''; }
    const host = 'https://clearml.ccixgtestbed.org';
    if (this.selectedTab() === 'python') {
      return `api {\n  web_server: ${host}/\n  api_server: ${host}/api\n  files_server: ${host}/files\n  credentials {\n    "access_key" = "${c.access_key}"\n    "secret_key" = "${c.secret_key}"\n  }\n}`;
    }
    return `%env CLEARML_WEB_HOST="${host}/"\n%env CLEARML_API_HOST="${host}/api"\n%env CLEARML_FILES_HOST="${host}/files"\n%env CLEARML_API_ACCESS_KEY="${c.access_key}"\n%env CLEARML_API_SECRET_KEY="${c.secret_key}"`;
  }

  protected async copyConfiguration(): Promise<void> {
    if (!this.shownCredentials()) {return;}
    try {
      await navigator.clipboard.writeText(this.configurationText());
    } catch {
      this.requestError.set('Clipboard permission denied. Select and copy the configuration manually.');
    }
  }

  protected closeCredentialDialog(): void {
    this.shownCredentials.set(null); // Discard secret from component state after display.
    this.requestError.set('');
  }
}
