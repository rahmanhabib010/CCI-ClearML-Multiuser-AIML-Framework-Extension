import {ChangeDetectorRef, Component, Input, OnDestroy, OnInit, TemplateRef, ViewChild} from '@angular/core';
import {Observable, Subscription} from 'rxjs';
import {CredentialKeyExt, selectCredentials, selectNewCredential} from '@common/core/reducers/common-auth-reducer';
import {selectCurrentUser} from '@common/core/reducers/users-reducer';
import {filter, take} from 'rxjs/operators';
import {createCredential, credentialRevoked, getAllCredentials, resetCredential, updateCredentialLabel} from '@common/core/actions/common-auth.actions';
import {Store} from '@ngrx/store';
import {GetCurrentUserResponseUserObject} from '~/business-logic/model/users/getCurrentUserResponseUserObject';
import {MatDialog} from '@angular/material/dialog';
import {CreateCredentialDialogComponent} from '~/features/settings/containers/admin/create-credential-dialog/create-credential-dialog.component';
import {ApiAuthService} from '~/business-logic/api-services/auth.service';

interface ManagedTarget {
  id: string;
  name: string;
  email: string;
  kind: 'user' | 'worker';
  credentials: {access_key: string; label: string; last_used?: string}[];
}

@Component({
  selector: 'sm-user-credentials',
  templateUrl: './user-credentials.component.html',
  styleUrls: ['./user-credentials.component.scss'],
  standalone: false
})
export class UserCredentialsComponent implements OnInit, OnDestroy {

  @Input() canManageCredentials = false;
  public credentials$: Observable<Record<string, CredentialKeyExt[]>>;
  private newCredentialSub: Subscription;
  creatingCredentials = false;
  private user: GetCurrentUserResponseUserObject;
  managedTargets: ManagedTarget[] = [];
  @ViewChild('credentialTargetDialog') credentialTargetDialog: TemplateRef<any>;
  selectedAccountType: 'admin' | 'user' | 'worker' = 'user';
  selectedTargetId = '';
  credentialLabel = '';
  workerName = '';
  managementError = '';
  managementBusy = false;
  constructor(private store: Store, public dialog: MatDialog, private authApi: ApiAuthService, private cdr: ChangeDetectorRef) { }

  get currentUserId(): string {
    return this.user?.id || '';
  }

  get filteredTargets(): ManagedTarget[] {
    return this.managedTargets.filter(target => target.kind === this.selectedAccountType);
  }

  get selectedTarget(): ManagedTarget | undefined {
    return this.managedTargets.find(target => target.id === this.selectedTargetId);
  }

  loadManagedTargets() {
    if (!this.isCredentialAdmin()) { return; }
    this.authApi.authAdminCredentialTargets().subscribe({
      next: result => {
        this.managedTargets = result?.targets || [];
        if (!this.managedTargets.some(target => target.id === this.selectedTargetId)) {
          this.selectedTargetId = '';
        }
        this.managementError = '';
        this.cdr.detectChanges();
      },
      error: () => {
        this.managementError = 'Unable to load accounts. Check the API server patch.';
        this.cdr.detectChanges();
      }
    });
  }

  changeAccountType() {
    this.selectedTargetId = '';
  }

  issueUserCredential() {
    if (!this.isCredentialAdmin() || !this.selectedTargetId || this.managementBusy) { return; }
    this.managementBusy = true;
    this.authApi.authAdminCreateUserCredentials(this.selectedTargetId, this.credentialLabel).subscribe({
      next: response => {
        this.managementBusy = false;
        const credential = response?.credentials;
        if (credential?.secret_key) {
          this.dialog.closeAll();
          this.openCreatedCredentialDialog(credential, true);
        }
        this.credentialLabel = '';
        this.loadManagedTargets();
        this.cdr.detectChanges();
      },
      error: () => {
        this.managementBusy = false;
        this.managementError = 'Unable to issue credential. Check your selected account.';
      }
    });
  }

  revokeUserCredential(userId: string, accessKey: string) {
    if (!this.isCredentialAdmin() || !userId || this.managementBusy) { return; }
    if (!window.confirm('Revoke this credential? Any SDK or worker using it will lose access.')) { return; }
    this.managementBusy = true;
    this.authApi.authAdminRevokeUserCredentials(userId, accessKey).subscribe({
      next: () => {
        this.managementBusy = false;
        // The API has confirmed revocation. Update this row immediately; avoid
        // an additional account-list round trip on every revoke.
        this.managedTargets = this.managedTargets.map(target => target.id === userId ? {
          ...target,
          credentials: target.credentials.filter(credential => credential.access_key !== accessKey)
        } : target);
        this.cdr.detectChanges();
      },
      error: () => {
        this.managementBusy = false;
        this.managementError = 'Credential revocation failed.';
      }
    });
  }

  showCredential(userId: string, accessKey: string) {
    if (!this.isCredentialAdmin() || this.managementBusy) { return; }
    this.managementBusy = true;
    this.managementError = '';
    this.authApi.authAdminShowCredentials(userId, accessKey).subscribe({
      next: response => {
        this.managementBusy = false;
        if (response?.credentials?.secret_key) {
          this.openCreatedCredentialDialog(response.credentials, true);
        } else {
          this.managementError = 'The credential details were not returned by the server.';
        }
      },
      error: () => {
        this.managementBusy = false;
        this.managementError = 'Unable to show credentials. Check the server patch and admin session.';
      }
    });
  }

  registerWorkerIdentity() {
    if (!this.isCredentialAdmin() || this.managementBusy) { return; }
    this.managementBusy = true;
    this.authApi.authAdminCreateWorkerIdentity(this.workerName).subscribe({
      next: worker => {
        this.managementBusy = false;
        this.workerName = '';
        this.authApi.authAdminCredentialTargets().subscribe({
          next: result => {
            this.managedTargets = result?.targets || [];
            this.selectedAccountType = 'worker';
            this.selectedTargetId = worker.id;
            this.managementError = '';
            this.cdr.detectChanges();
          },
          error: () => {
            this.managementError = 'Worker created, but account refresh failed.';
            this.cdr.detectChanges();
          }
        });
      },
      error: () => {
        this.managementBusy = false;
        this.managementError = 'Unable to register worker identity. Name must be unique and valid.';
      }
    });
  }

  ngOnInit(): void {
    this.store.select(selectCurrentUser)
      .pipe(filter(user => !!user), take(1))
      .subscribe(user => {
        this.user = user;
        if (this.isCredentialAdmin()) {
          this.store.dispatch(getAllCredentials({userId: ''}));
          this.loadManagedTargets();
        }
      });
    this.credentials$ = this.store.select(selectCredentials);

    this.newCredentialSub = this.store.select(selectNewCredential)
      .subscribe(() => {
        this.creatingCredentials = false;
      });
  }

  createCredential() {
    if (!this.isCredentialAdmin()) { return; }
    this.managementError = '';
    this.selectedAccountType = 'user';
    this.selectedTargetId = '';
    this.credentialLabel = '';
    this.loadManagedTargets();
    this.dialog.open(this.credentialTargetDialog, {
      width: '680px',
      maxWidth: 'calc(100vw - 32px)',
      panelClass: 'clearml-credential-selection-panel',
    });
  }

  confirmCredentialTarget() {
    if (this.managementBusy) { return; }
    if (this.selectedAccountType === 'admin') {
      this.dialog.closeAll();
      this.createAdminCredential();
    } else {
      this.issueUserCredential();
    }
  }

  private openCreatedCredentialDialog(credentials: any, managed: boolean) {
    this.dialog.open(CreateCredentialDialogComponent, {
        data: {workspace: this.user.company, credentials, managed},
        width: '788px'
    }).afterClosed().subscribe(() => {
      this.creatingCredentials = false;
      this.store.dispatch(resetCredential());
      this.store.dispatch(getAllCredentials({userId: ''}));
      this.loadManagedTargets();
    });
  }

  private createAdminCredential() {
    this.creatingCredentials = true;
    this.openCreatedCredentialDialog(null, false);
    this.store.dispatch(createCredential({workspace: this.user.company, openCredentialsPopup: true}));
  }

  onCredentialRevoked(accessKey) {
    if (!this.isCredentialAdmin()) { return; }
    this.store.dispatch(credentialRevoked({accessKey, workspaceId: this.user.company.id}));
  }

  updateLabel({credential, label}) {
    if (!this.isCredentialAdmin()) { return; }
    this.store.dispatch(updateCredentialLabel({credential: {...credential, company: this.user.company.id}, label}));
  }

  editAdminLabel(credential: CredentialKeyExt) {
    if (!this.isCredentialAdmin()) { return; }
    const label = window.prompt('Credential label', credential.label || '');
    if (label !== null && label !== credential.label) {
      this.updateLabel({credential, label});
    }
  }

  private isCredentialAdmin(): boolean {
    return this.canManageCredentials;
  }

  ngOnDestroy(): void {
    this.newCredentialSub.unsubscribe();
  }
}
