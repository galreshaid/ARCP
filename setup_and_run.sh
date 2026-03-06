#!/bin/bash
# AAML RadCore Platform - Setup and Run Script

echo "🚀 AAML RadCore Platform Setup"
echo "====================="

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "${YELLOW}Creating virtual environment...${NC}"
    python3 -m venv venv
fi

# Activate virtual environment
echo "${GREEN}Activating virtual environment...${NC}"
source venv/bin/activate

# Install requirements
echo "${GREEN}Installing requirements...${NC}"
pip install -r requirements.txt

# Create necessary directories
echo "${GREEN}Creating directories...${NC}"
mkdir -p logs
mkdir -p media/evidence
mkdir -p staticfiles
mkdir -p data

# Create .env file if doesn't exist
if [ ! -f ".env" ]; then
    echo "${YELLOW}Creating .env file...${NC}"
    cat > .env << EOF
# Django Settings
DEBUG=True
SECRET_KEY=django-insecure-dev-key-change-in-production
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
DB_NAME=aip_db
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432

# Celery & Redis
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# HL7
HL7_MIRTH_HOST=localhost
HL7_MIRTH_PORT=6661
HL7_FACILITY=HOSPITAL

# Deep Links
DEEPLINK_SECRET_KEY=your-secret-key-here
SITE_URL=http://localhost:8000
EOF
    echo "${GREEN}.env file created. Please review and update values.${NC}"
fi

# Run migrations
echo "${GREEN}Running migrations...${NC}"
python manage.py makemigrations
python manage.py migrate

# Create superuser
echo "${YELLOW}Create superuser? (y/n)${NC}"
read -r create_superuser
if [ "$create_superuser" = "y" ]; then
    python manage.py createsuperuser
fi

# Collect static files
echo "${GREEN}Collecting static files...${NC}"
python manage.py collectstatic --noinput

echo ""
echo "${GREEN}✅ Setup complete!${NC}"
echo ""
echo "To start the server, run:"
echo "  python manage.py runserver"
echo ""
echo "Admin panel: http://localhost:8000/admin/"
echo "API docs: http://localhost:8000/api/schema/swagger/"
echo ""