# Pocketful stage 1. The service uses only the Node.js runtime: no packages are
# fetched at build or run time, and nothing reaches the network once started.
FROM node:22-alpine
WORKDIR /app
COPY package.json server.js ./
COPY src ./src
ENV NODE_ENV=production \
    PORT=8080
EXPOSE 8080
USER node
CMD ["node", "--max-old-space-size=1536", "server.js"]
