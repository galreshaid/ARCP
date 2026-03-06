"""
HL7 Integration Models
"""
from django.db import models
from django.utils.translation import gettext_lazy as _
from apps.core.models import BaseModel, Exam


class HL7Message(BaseModel):
    """
    Store incoming and outgoing HL7 messages for audit
    """
    DIRECTION_CHOICES = [
        ('INBOUND', 'Inbound'),
        ('OUTBOUND', 'Outbound'),
    ]
    
    STATUS_CHOICES = [
        ('RECEIVED', 'Received'),
        ('PROCESSING', 'Processing'),
        ('PROCESSED', 'Processed'),
        ('SENT', 'Sent'),
        ('ERROR', 'Error'),
        ('REJECTED', 'Rejected'),
    ]
    
    # Message info
    direction = models.CharField(_('Direction'), max_length=10, choices=DIRECTION_CHOICES, db_index=True)
    message_type = models.CharField(_('Message Type'), max_length=50)  # ORM^O01, ORR^O02, etc.
    message_control_id = models.CharField(_('Message Control ID'), max_length=100, db_index=True)
    
    # Content
    raw_message = models.TextField(_('Raw HL7 Message'))
    parsed_data = models.JSONField(_('Parsed Data'), default=dict, blank=True)
    
    # Status
    status = models.CharField(_('Status'), max_length=20, choices=STATUS_CHOICES, default='RECEIVED', db_index=True)
    error_message = models.TextField(_('Error Message'), blank=True)
    
    # Related entities
    exam = models.ForeignKey(
        Exam,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='hl7_messages'
    )
    
    # Sending/Receiving system
    sending_application = models.CharField(_('Sending Application'), max_length=100, blank=True)
    sending_facility = models.CharField(_('Sending Facility'), max_length=100, blank=True)
    receiving_application = models.CharField(_('Receiving Application'), max_length=100, blank=True)
    receiving_facility = models.CharField(_('Receiving Facility'), max_length=100, blank=True)
    
    # Processing
    processed_at = models.DateTimeField(_('Processed At'), null=True, blank=True)
    processing_duration_ms = models.IntegerField(_('Processing Duration (ms)'), null=True, blank=True)
    
    # Response (for outbound messages)
    response_message = models.TextField(_('Response Message'), blank=True)
    response_received_at = models.DateTimeField(_('Response Received At'), null=True, blank=True)

    class Meta:
        verbose_name = _('HL7 Message')
        verbose_name_plural = _('HL7 Messages')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['direction', 'status', '-created_at']),
            models.Index(fields=['message_control_id']),
            models.Index(fields=['exam', '-created_at']),
        ]

    def __str__(self):
        return f"{self.direction} {self.message_type} - {self.message_control_id}"

    def exam_accession_number(self):
        if not self.exam_id:
            return ''
        return self.exam.accession_number

    exam_accession_number.short_description = 'Acc#'

    def exam_order_number(self):
        if not self.exam_id:
            return ''
        return self.exam.order_id

    exam_order_number.short_description = 'Order#'


class HL7Configuration(BaseModel):
    """
    HL7 Integration configuration per facility
    """
    facility = models.ForeignKey(
        'core.Facility',
        on_delete=models.CASCADE,
        related_name='hl7_configurations'
    )
    
    # Connection settings
    mirth_host = models.CharField(_('Mirth Host'), max_length=100, default='localhost')
    mirth_port = models.IntegerField(_('Mirth Port'), default=6661)
    
    # Application/Facility IDs
    sending_application = models.CharField(_('Sending Application'), max_length=100, default='AIP')
    sending_facility = models.CharField(_('Sending Facility'), max_length=100)
    
    # Settings
    is_active = models.BooleanField(_('Is Active'), default=True)
    auto_send_orr = models.BooleanField(_('Auto Send ORR'), default=True)
    retry_on_failure = models.BooleanField(_('Retry on Failure'), default=True)
    max_retry_attempts = models.IntegerField(_('Max Retry Attempts'), default=3)
    
    class Meta:
        verbose_name = _('HL7 Configuration')
        verbose_name_plural = _('HL7 Configurations')
        unique_together = ['facility']

    def __str__(self):
        return f"HL7 Config - {self.facility.code}"
