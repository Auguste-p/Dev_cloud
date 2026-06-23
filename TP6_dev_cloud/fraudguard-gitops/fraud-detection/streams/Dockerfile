FROM node:20-alpine
WORKDIR /app
COPY package.json .
RUN npm install --production
COPY fraud-detector.js .
CMD ["node", "fraud-detector.js"]