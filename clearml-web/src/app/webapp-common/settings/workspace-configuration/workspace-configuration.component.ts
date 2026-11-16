import {Component, inject, OnInit, signal} from '@angular/core';
import {selectCurrentUser} from '@common/core/reducers/users-reducer';
import {Store} from '@ngrx/store';
import {ApiAuthService} from '~/business-logic/api-services/auth.service';

@Component({
    selector: 'sm-workspace-configuration',
    templateUrl: './workspace-configuration.component.html',
    styleUrls: ['./workspace-configuration.component.scss'],
    standalone: false
})
export class WorkspaceConfigurationComponent implements OnInit {

  private store = inject(Store);
  private authApi = inject(ApiAuthService);
  protected currentUser = this.store.selectSignal(selectCurrentUser);
  protected canManageApiCredentials = signal(false);

  ngOnInit(): void {
    this.authApi.authCredentialAdminStatus().subscribe({
      next: result => this.canManageApiCredentials.set(result?.can_manage_credentials === true),
      error: () => this.canManageApiCredentials.set(false)
    });
  }

}
