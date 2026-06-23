FROM node:20-alpine
WORKDIR /app
COPY package.json .
RUN npm install --production
COPY transaction-producer.js .
CMD ["node", "transaction-producer.js"]