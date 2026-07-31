#!/bin/bash
# Setup script per Digitació API al servidor
# Instal·la Audiveris (OMR), Verovio (renderitzat) i dependències

set -e
echo "=== Digitació: Instal·lant dependències ==="

# Dependències del sistema
echo ">>> Instal·lant paquets del sistema..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    openjdk-17-jdk \
    xvfb \
    poppler-utils \
    python3-pip \
    python3-venv \
    git \
    libcairo2-dev \
    pkg-config \
    tesseract-ocr

# Entorn virtual Python
echo ">>> Creant entorn virtual Python..."
VENV_DIR="/home/ubuntu/digitacio-venv"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

echo ">>> Instal·lant paquets Python..."
pip install --upgrade pip
pip install flask flask-cors verovio cairosvg pypdf

# Audiveris (OMR)
echo ">>> Instal·lant Audiveris..."
AUDIVERIS_DIR="/opt/audiveris"
if [ ! -d "$AUDIVERIS_DIR" ]; then
    sudo mkdir -p "$AUDIVERIS_DIR"
    sudo chown ubuntu:ubuntu "$AUDIVERIS_DIR"
    cd "$AUDIVERIS_DIR"
    git clone --depth 1 https://github.com/Audiveris/audiveris.git .
    # Build amb Gradle
    chmod +x gradlew
    ./gradlew build -x test 2>&1 | tail -5
    echo ">>> Audiveris compilat correctament"
else
    echo ">>> Audiveris ja existent a $AUDIVERIS_DIR"
fi

# Verificar
echo ""
echo "=== Verificació ==="
java -version 2>&1 | head -1
echo "Audiveris JAR: $(ls $AUDIVERIS_DIR/build/libs/*.jar 2>/dev/null || echo 'NO TROBAT')"
python3 -c "import verovio; print(f'Verovio {verovio.toolkit().getVersion()}')"
python3 -c "import cairosvg; print('CairoSVG OK')"
echo ""

# Crear servei systemd
echo ">>> Creant servei systemd..."
sudo tee /etc/systemd/system/digitacio-api.service > /dev/null <<EOF
[Unit]
Description=Digitació API (Flask)
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/var/www/html/digitacio/api
Environment=AUDIVERIS_JAR=$AUDIVERIS_DIR/build/libs/audiveris.jar
ExecStart=$VENV_DIR/bin/python app.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable digitacio-api
sudo systemctl start digitacio-api

echo ""
echo "=== Setup complet! ==="
echo "API escoltant a http://localhost:5085"
echo "Comprova: curl http://localhost:5085/api/health"
