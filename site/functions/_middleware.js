// Cloudflare Pages edge middleware — HTTP Basic Auth gate for the whole site.
// Runs on EVERY request (including /data/leads.json) before any file is served, so
// the leads data is never reachable without the password. Works on the free
// *.pages.dev domain (unlike Cloudflare Access, which needs a custom domain).
//
// Set the password as a Pages environment variable named SITE_PASSWORD
// (Project → Settings → Variables and Secrets). Any username works; only the
// password is checked. Fails CLOSED: if SITE_PASSWORD is unset, nothing is served.

export async function onRequest({ request, env, next }) {
  const expected = env.SITE_PASSWORD;

  const deny = (msg) =>
    new Response(msg, {
      status: 401,
      headers: {
        "WWW-Authenticate": 'Basic realm="leads-viewer", charset="UTF-8"',
        "content-type": "text/plain; charset=utf-8",
      },
    });

  if (!expected) return deny("访问密码尚未配置，请联系管理员设置 SITE_PASSWORD。");

  const header = request.headers.get("Authorization") || "";
  if (header.startsWith("Basic ")) {
    let decoded = "";
    try {
      decoded = atob(header.slice(6));
    } catch (e) {
      return deny("需要登录");
    }
    const password = decoded.slice(decoded.indexOf(":") + 1);
    if (password.length === expected.length && password === expected) {
      return next();
    }
  }
  return deny("需要登录");
}
