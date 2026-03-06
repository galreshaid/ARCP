from django import forms

from apps.core.constants import PROTOCOL_REQUIRED_MODALITY_CODES
from apps.core.models import Modality, Procedure


_PROTOCOL_MODALITY_LABEL = ", ".join(PROTOCOL_REQUIRED_MODALITY_CODES)


class SystemAdminModalityForm(forms.ModelForm):
    class Meta:
        model = Modality
        fields = (
            'code',
            'name',
            'description',
            'is_active',
            'requires_qc',
            'requires_contrast',
            'qc_checklist_template',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['code'].help_text = (
            f"Protocol workflow currently uses these modality codes: {_PROTOCOL_MODALITY_LABEL}."
        )
        self.fields['is_active'].help_text = (
            "Active modalities can appear in the Protocol Worklist when their code is protocol-enabled."
        )
        self.fields['requires_contrast'].help_text = (
            "Enable or disable this modality in the Contrast & Materials worklist."
        )


class SystemAdminProcedureForm(forms.ModelForm):
    class Meta:
        model = Procedure
        fields = ('code', 'name', 'modality', 'body_region', 'is_active', 'metadata')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['modality'].queryset = Modality.objects.order_by('code')
        self.fields['modality'].help_text = (
            f"Only procedures under {_PROTOCOL_MODALITY_LABEL} can appear in the Protocol Worklist."
        )
        self.fields['is_active'].help_text = (
            "If a matching procedure is inactive, that exam is hidden from the Protocol Worklist."
        )
