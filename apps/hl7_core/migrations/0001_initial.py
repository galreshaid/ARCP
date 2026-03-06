from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='HL7Message',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('direction', models.CharField(choices=[('INBOUND', 'Inbound'), ('OUTBOUND', 'Outbound')], db_index=True, max_length=10, verbose_name='Direction')),
                ('message_type', models.CharField(max_length=50, verbose_name='Message Type')),
                ('message_control_id', models.CharField(db_index=True, max_length=100, verbose_name='Message Control ID')),
                ('raw_message', models.TextField(verbose_name='Raw HL7 Message')),
                ('parsed_data', models.JSONField(blank=True, default=dict, verbose_name='Parsed Data')),
                ('status', models.CharField(choices=[('RECEIVED', 'Received'), ('PROCESSING', 'Processing'), ('PROCESSED', 'Processed'), ('SENT', 'Sent'), ('ERROR', 'Error'), ('REJECTED', 'Rejected')], db_index=True, default='RECEIVED', max_length=20, verbose_name='Status')),
                ('error_message', models.TextField(blank=True, verbose_name='Error Message')),
                ('sending_application', models.CharField(blank=True, max_length=100, verbose_name='Sending Application')),
                ('sending_facility', models.CharField(blank=True, max_length=100, verbose_name='Sending Facility')),
                ('receiving_application', models.CharField(blank=True, max_length=100, verbose_name='Receiving Application')),
                ('receiving_facility', models.CharField(blank=True, max_length=100, verbose_name='Receiving Facility')),
                ('processed_at', models.DateTimeField(blank=True, null=True, verbose_name='Processed At')),
                ('processing_duration_ms', models.IntegerField(blank=True, null=True, verbose_name='Processing Duration (ms)')),
                ('response_message', models.TextField(blank=True, verbose_name='Response Message')),
                ('response_received_at', models.DateTimeField(blank=True, null=True, verbose_name='Response Received At')),
                ('exam', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='hl7_messages', to='core.exam')),
            ],
            options={
                'verbose_name': 'HL7 Message',
                'verbose_name_plural': 'HL7 Messages',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['direction', 'status', '-created_at'], name='hl7_core_hl_directi_81d262_idx'),
                    models.Index(fields=['message_control_id'], name='hl7_core_hl_message_357be5_idx'),
                    models.Index(fields=['exam', '-created_at'], name='hl7_core_hl_exam_id_5410dc_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='HL7Configuration',
            fields=[
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='Created At')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Updated At')),
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('mirth_host', models.CharField(default='localhost', max_length=100, verbose_name='Mirth Host')),
                ('mirth_port', models.IntegerField(default=6661, verbose_name='Mirth Port')),
                ('sending_application', models.CharField(default='AIP', max_length=100, verbose_name='Sending Application')),
                ('sending_facility', models.CharField(max_length=100, verbose_name='Sending Facility')),
                ('is_active', models.BooleanField(default=True, verbose_name='Is Active')),
                ('auto_send_orr', models.BooleanField(default=True, verbose_name='Auto Send ORR')),
                ('retry_on_failure', models.BooleanField(default=True, verbose_name='Retry on Failure')),
                ('max_retry_attempts', models.IntegerField(default=3, verbose_name='Max Retry Attempts')),
                ('facility', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='hl7_configuration', to='core.facility')),
            ],
            options={
                'verbose_name': 'HL7 Configuration',
                'verbose_name_plural': 'HL7 Configurations',
            },
        ),
    ]
