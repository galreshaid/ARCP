from django.contrib import admin

from apps.hl7_core.models import HL7Configuration, HL7Message


@admin.register(HL7Message)
class HL7MessageAdmin(admin.ModelAdmin):
    list_display = (
        'created_at',
        'direction',
        'message_type',
        'message_control_id',
        'exam_order_number',
        'exam_accession_number',
        'status',
        'exam',
        'sending_facility',
        'receiving_facility',
    )
    list_filter = ('direction', 'status', 'message_type', 'created_at')
    search_fields = (
        'message_control_id',
        'message_type',
        'exam__accession_number',
        'exam__order_id',
        'raw_message',
    )
    readonly_fields = (
        'id',
        'created_at',
        'updated_at',
        'direction',
        'message_type',
        'message_control_id',
        'raw_message',
        'parsed_data',
        'status',
        'error_message',
        'exam',
        'sending_application',
        'sending_facility',
        'receiving_application',
        'receiving_facility',
        'processed_at',
        'processing_duration_ms',
        'response_message',
        'response_received_at',
    )
    ordering = ('-created_at',)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HL7Configuration)
class HL7ConfigurationAdmin(admin.ModelAdmin):
    list_display = (
        'facility',
        'mirth_host',
        'mirth_port',
        'sending_application',
        'sending_facility',
        'is_active',
        'auto_send_orr',
    )
    list_filter = ('is_active', 'auto_send_orr', 'retry_on_failure')
    search_fields = ('facility__code', 'facility__name', 'sending_facility', 'sending_application')
