# nginx-certbot с DNS-01 плагином Spaceship — выпуск/продление серта bi.apple-certificate.solutions.
# Базовый образ (nginx + certbot + автопродление) плагина Spaceship не несёт — доставляем pip'ом.
# Debian-база (НЕ -alpine): под musl нет колёс, сборка из исходников дольше и ломается.
ARG NGINX_CERTBOT_BASE_TAG=6.2.0
FROM jonasal/nginx-certbot:${NGINX_CERTBOT_BASE_TAG}

# Версия плагина ПИНИТСЯ: сторонний маленький проект, «latest» = «неизвестно что приедет».
ARG CERTBOT_DNS_SPACESHIP_VERSION=1.0.4
RUN pip install --no-cache-dir --break-system-packages \
      certbot-dns-spaceship==${CERTBOT_DNS_SPACESHIP_VERSION}
