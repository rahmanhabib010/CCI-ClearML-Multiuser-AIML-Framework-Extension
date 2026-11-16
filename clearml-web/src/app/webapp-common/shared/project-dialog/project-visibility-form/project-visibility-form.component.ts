import {
  Component,
  EventEmitter,
  Input,
  Output,
  OnChanges,
  SimpleChanges
} from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatRadioModule } from '@angular/material/radio';
import { MatButtonModule } from '@angular/material/button';
import { Project } from '~/business-logic/model/projects/project';

@Component({
  selector: 'sm-project-visibility-form',
  standalone: true,
  imports: [CommonModule, FormsModule, MatRadioModule, MatButtonModule],
  templateUrl: './project-visibility-form.component.html',
  styleUrls: ['./project-visibility-form.component.scss']
})
export class ProjectVisibilityFormComponent implements OnChanges {
  @Input() baseProject: Project;
  @Output() setVisibility = new EventEmitter<{ visibility: 'private' | 'public' }>();
  @Output() dismissDialog = new EventEmitter<void>();

  visibility: 'private' | 'public' = 'private';

  ngOnChanges(changes: SimpleChanges) {
    if (changes['baseProject']?.currentValue) {
      const project = changes['baseProject'].currentValue as any;

      console.log('baseProject on reopen = ', project);
      console.log('baseProject.visibility = ', project?.visibility);

      this.visibility = project?.visibility === 'public' ? 'public' : 'private';
    }
  }

  submit() {
    console.log('child submit visibility = ', this.visibility);
    console.log('child baseProject = ', this.baseProject);
    this.setVisibility.emit({ visibility: this.visibility });
  }

  cancel() {
    this.dismissDialog.emit();
  }
}
