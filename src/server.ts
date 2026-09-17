import index from "./index.html";

const port = Number(process.env.PORT ?? 4187);

const server = Bun.serve({
  port,
  routes: {
    "/": index,
    "/api/health": () => Response.json({ ok: true }),
  },
});

console.log(`Dictum listening on ${server.url}`);
