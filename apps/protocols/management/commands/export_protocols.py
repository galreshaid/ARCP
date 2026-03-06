"""
Management Command: Export Protocols to CSV
"""
import csv
from django.core.management.base import BaseCommand
from apps.protocols.models import ProtocolTemplate


class Command(BaseCommand):
    help = 'Export protocols to CSV file'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'output_file',
            type=str,
            help='Output CSV file path'
        )
        parser.add_argument(
            '--modality',
            type=str,
            help='Filter by modality code'
        )
        parser.add_argument(
            '--facility',
            type=str,
            help='Filter by facility code'
        )
        parser.add_argument(
            '--active-only',
            action='store_true',
            help='Export only active protocols'
        )

    def handle(self, *args, **options):
        output_file = options['output_file']
        
        # Build queryset
        qs = ProtocolTemplate.objects.all()
        
        if options['modality']:
            qs = qs.filter(modality__code=options['modality'])
        
        if options['facility']:
            qs = qs.filter(facility__code=options['facility'])
        
        if options['active_only']:
            qs = qs.filter(is_active=True)
        
        qs = qs.select_related('modality', 'facility')
        
        # Export to CSV
        with open(output_file, 'w', newline='', encoding='utf-8') as csvfile:
            fieldnames = [
                'code', 'name', 'display_name', 'modality_code', 'facility_code',
                'body_part', 'body_part_code', 'laterality',
                'requires_contrast', 'contrast_phase',
                'description', 'instructions',
                'clinical_keywords', 'priority',
                'is_active', 'is_default', 'version', 'usage_count'
            ]
            
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            
            count = 0
            for protocol in qs:
                writer.writerow({
                    'code': protocol.code,
                    'name': protocol.name,
                    'display_name': protocol.display_name or '',
                    'modality_code': protocol.modality.code,
                    'facility_code': protocol.facility.code if protocol.facility else '',
                    'body_part': protocol.body_part,
                    'body_part_code': protocol.body_part_code or '',
                    'laterality': protocol.laterality,
                    'requires_contrast': protocol.requires_contrast,
                    'contrast_phase': protocol.contrast_phase,
                    'description': protocol.description or '',
                    'instructions': protocol.instructions or '',
                    'clinical_keywords': ','.join(protocol.clinical_keywords),
                    'priority': protocol.priority,
                    'is_active': protocol.is_active,
                    'is_default': protocol.is_default,
                    'version': protocol.version,
                    'usage_count': protocol.usage_count,
                })
                count += 1
        
        self.stdout.write(
            self.style.SUCCESS(f'Successfully exported {count} protocols to {output_file}')
        )