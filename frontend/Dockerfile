FROM nginx:alpine

# Copy static files
COPY index.html /usr/share/nginx/html/index.html

# Inject API_BASE via nginx env sub — or use the default (localhost)
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
