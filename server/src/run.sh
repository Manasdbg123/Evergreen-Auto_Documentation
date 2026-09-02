pm2 delete diff-engine
pm2 start dist/src/server.js --name "diff-engine" --update-env -- --env .env
pm2 logs diff-engine --lines 20