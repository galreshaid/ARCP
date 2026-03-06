"""
Management Command: Import Protocols from CSV/Excel
يسمح بإضافة البروتوكولات بكميات كبيرة من ملفات CSV أو Excel
"""
import csv
from django.core.management.base import BaseCommand, CommandError
from apps.protocols.models import ProtocolTemplate
from apps.core.models import Modality, Facility


class Command(BaseCommand):
    help = 'Import protocols from CSV file'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'file_path',
            type=str,
            help='Path to CSV file'
        )
        parser.add_argument(
            '--update',
            action='store_true',
            help='Update existing protocols instead of creating new ones'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulate import without saving'
        )

    def handle(self, *args, **options):
        file_path = options['file_path']
        update_mode = options['update']
        dry_run = options['dry_run']
        
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE - No data will be saved'))
        
        try:
            with open(file_path, 'r', encoding='utf-8') as csvfile:
                reader = csv.DictReader(csvfile)
                
                created_count = 0
                updated_count = 0
                error_count = 0
                
                for row_num, row in enumerate(reader, start=2):
                    try:
                        result = self.process_row(row, update_mode, dry_run)
                        
                        if result == 'created':
                            created_count += 1
                        elif result == 'updated':
                            updated_count += 1
                    
                    except Exception as e:
                        error_count += 1
                        self.stdout.write(
                            self.style.ERROR(f'Row {row_num}: {str(e)}')
                        )
                
                # Summary
                self.stdout.write(self.style.SUCCESS(
                    f'\nImport Summary:\n'
                    f'  Created: {created_count}\n'
                    f'  Updated: {updated_count}\n'
                    f'  Errors: {error_count}'
                ))
                
                if dry_run:
                    self.stdout.write(self.style.WARNING(
                        '\nDRY RUN - No actual changes were made'
                    ))
        
        except FileNotFoundError:
            raise CommandError(f'File not found: {file_path}')
        
        except Exception as e:
            raise CommandError(f'Import failed: {str(e)}')

    def process_row(self, row, update_mode, dry_run):
        """
        Process a single CSV row
        
        Expected CSV columns:
        - code (required)
        - name (required)
        - modality_code (required)
        - facility_code (optional)
        - body_part
        - laterality
        - requires_contrast
        - contrast_phase
        - description
        - instructions
        - clinical_keywords (comma-separated)
        - priority
        - is_active
        - is_default
        """
        # Required fields
        code = row.get('code', '').strip()
        name = row.get('name', '').strip()
        modality_code = row.get('modality_code', '').strip()
        
        if not code or not name or not modality_code:
            raise ValueError('Missing required fields: code, name, or modality_code')
        
        # Get or create modality
        try:
            modality = Modality.objects.get(code=modality_code)
        except Modality.DoesNotExist:
            raise ValueError(f'Modality not found: {modality_code}')
        
        # Get facility (optional)
        facility = None
        facility_code = row.get('facility_code', '').strip()
        if facility_code:
            try:
                facility = Facility.objects.get(code=facility_code)
            except Facility.DoesNotExist:
                raise ValueError(f'Facility not found: {facility_code}')
        
        # Parse boolean fields
        requires_contrast = row.get('requires_contrast', 'false').lower() in ['true', '1', 'yes']
        is_active = row.get('is_active', 'true').lower() in ['true', '1', 'yes']
        is_default = row.get('is_default', 'false').lower() in ['true', '1', 'yes']
        
        # Parse clinical keywords
        clinical_keywords = []
        keywords_str = row.get('clinical_keywords', '').strip()
        if keywords_str:
            clinical_keywords = [k.strip() for k in keywords_str.split(',')]
        
        # Parse priority
        try:
            priority = int(row.get('priority', '100'))
        except ValueError:
            priority = 100
        
        # Prepare data
        protocol_data = {
            'name': name,
            'modality': modality,
            'facility': facility,
            'body_part': row.get('body_part', '').strip(),
            'laterality': row.get('laterality', 'NOT_APPLICABLE'),
            'requires_contrast': requires_contrast,
            'contrast_phase': row.get('contrast_phase', 'NONE'),
            'description': row.get('description', '').strip(),
            'instructions': row.get('instructions', '').strip(),
            'clinical_keywords': clinical_keywords,
            'priority': priority,
            'is_active': is_active,
            'is_default': is_default,
        }
        
        if dry_run:
            self.stdout.write(f'Would create/update: {code} - {name}')
            return 'created'
        
        # Create or update
        if update_mode:
            protocol, created = ProtocolTemplate.objects.update_or_create(
                code=code,
                defaults=protocol_data
            )
            return 'created' if created else 'updated'
        else:
            # Create only
            protocol = ProtocolTemplate.objects.create(
                code=code,
                **protocol_data
            )
            return 'created'