FROM node:20-alpine
WORKDIR /app
COPY package.json .
RUN npm install --production
COPY alert-handler.js .
CMD ["node", "alert-handler.js"]