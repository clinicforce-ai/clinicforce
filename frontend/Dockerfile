# Stage 1: Build the React app
FROM node:22.16.0-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Serve the app using Nginx
FROM nginx:1.27-alpine
# Copy the built files from Stage 1 into Nginx
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
# ECS overrides this with its Service Connect alias; no task IP is baked into the image.
ENV BACKEND_UPSTREAM=http://backend:8000
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
