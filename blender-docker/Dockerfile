FROM ubuntu:22.04

# Устанавливаем переменные окружения (предварительно)
ENV DEBIAN_FRONTEND=noninteractive

# Установка базовых зависимостей, включая python3-pip и python3-venv
# venv нужен, если Blender Python его использует для pip
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    locales \
    wget \
    bzip2 \
    xz-utils \
    unzip \
    python3 \
    python3-pip \
    python3-venv \
    # Основные библиотеки для графики и оконной системы (оставляем необходимые для headless Blender)
    libx11-6 \
    libgl1-mesa-glx \
    libxi6 \
    libxrender1 \
    libxxf86vm1 \
    libfontconfig1 \
    libfreetype6 \
    libsm6 \
    libice6 \
    # Некоторые XCB библиотеки
    libxcb1 \
    libxcb-icccm4 \
    libxcb-image0 \
    libxcb-keysyms1 \
    libxcb-render-util0 \
    libxcb-xkb1 \
    libxkbcommon-x11-0 \
    && rm -rf /var/lib/apt/lists/*

# Генерируем локаль
RUN locale-gen C.UTF-8

# Устанавливаем переменные окружения для локали и Python
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV LC_CTYPE=C.UTF-8
ENV LC_MESSAGES=C.UTF-8
ENV PYTHONIOENCODING=utf-8
ENV PYTHONUNBUFFERED=1
ENV TERM=xterm
ENV PYTHONLEGACYWINDOWSSTDIO=utf-8

# Скачиваем и устанавливаем Blender
RUN wget https://download.blender.org/release/Blender3.6/blender-3.6.5-linux-x64.tar.xz \
    && tar -xf blender-3.6.5-linux-x64.tar.xz \
    && mv blender-3.6.5-linux-x64 /opt/blender \
    && rm blender-3.6.5-linux-x64.tar.xz

# Создание симлинка для Blender
RUN ln -s /opt/blender/blender /usr/local/bin/blender

# Создаем необходимые директории
RUN mkdir -p /app /input /output

# Устанавливаем рабочую директорию ПЕРЕД копированием
WORKDIR /app

# Копируем файл с зависимостями
COPY requirements.txt /app/

# Находим Python Blender'а и устанавливаем зависимости ИМЕННО для него
# Сначала ищем по стандартному пути, потом пробуем шире
RUN BLENDER_PYTHON_BIN=$(find /opt/blender/ -type f -executable -path '*/python/bin/python*' | head -n 1) && \
    if [ -z "$BLENDER_PYTHON_BIN" ]; then \
        echo "Standard Blender Python path not found, trying broader search..."; \
        # Ищем любой исполняемый python в подпапках bin
        BLENDER_PYTHON_BIN=$(find /opt/blender/ -name python* -type f -executable | grep '/bin/python' | head -n 1) ; \
    fi && \
    if [ -z "$BLENDER_PYTHON_BIN" ]; then \
        echo "Python executable for Blender could not be found!"; \
        # Выводим структуру для отладки
        find /opt/blender/ -name python* ; \
        exit 1; \
    fi && \
    echo "Found Blender Python at: $BLENDER_PYTHON_BIN" && \
    $BLENDER_PYTHON_BIN -m pip install --no-cache-dir -r /app/requirements.txt

# Копируем скрипты
# COPY check_model.py /app/
# COPY check_model.sh /app/
# Если есть папка addons, которую использует check_model.py, ее тоже нужно скопировать
COPY addons/ /app/addons/

# Устанавливаем переменные окружения для Blender
ENV BLENDER_PATH=/opt/blender/blender
ENV BLENDER_USER_SCRIPTS=/app
ENV BLENDER_SYSTEM_SCRIPTS=/app

# Запускаем скрипт (точка входа контейнера при запуске через docker run/docker-compose run)
# Если вы используете docker-compose up, эта CMD может быть переопределена
# CMD ["/app/check_model.sh"] # Оставим пока закомментированным, т.к. команда передается через docker-compose run