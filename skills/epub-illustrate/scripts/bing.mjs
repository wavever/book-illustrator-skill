// Appended to "const JOBS = [...]" by find_images.py and run with `ego-browser nodejs`.
// Bing Images search per job; saves the top original-size results as web_N.<ext> with the
// page each image came from (for checking identity/date) appended to SOURCE.json.
const fs = await import("node:fs/promises");
const task = await taskSpace("epub illustrate image search");
const page = task.page("p1");

for (const job of JOBS) {
  try {
    await page.goto("https://cn.bing.com/images/search?q=" + encodeURIComponent(job.query) + "&first=1");
    await page.waitForLoadState();
    await page.waitForTimeout(1500);
    const hits = await page.evaluate((limit) => {
      const out = [];
      for (const a of document.querySelectorAll("a.iusc")) {
        try {
          const m = JSON.parse(a.getAttribute("m"));
          if (m.murl) out.push({ img: m.murl, page: m.purl, title: m.t || "" });
        } catch {}
        if (out.length >= limit) break;
      }
      return out;
    }, job.limit || 8);
    const srcPath = job.dir + "/SOURCE.json";
    const sources = JSON.parse(await fs.readFile(srcPath, "utf-8").catch(() => "[]"));
    let n = sources.filter((s) => s.from === "web").length;
    for (const h of hits) {
      try {
        const ctl = AbortSignal.timeout(20000);
        const res = await fetch(h.img, { signal: ctl, headers: { "User-Agent": "Mozilla/5.0", Referer: h.page || "" } });
        if (!res.ok) continue;
        const type = res.headers.get("content-type") || "";
        if (!type.startsWith("image/")) continue;
        const buf = Buffer.from(await res.arrayBuffer());
        if (buf.length < 10000) continue;
        n += 1;
        const ext = type.includes("png") ? "png" : type.includes("webp") ? "webp" : "jpg";
        const file = `web_${n}.${ext}`;
        await fs.writeFile(`${job.dir}/${file}`, buf);
        sources.push({ file, from: "web", url: h.page, img: h.img, about: h.title, query: job.query });
      } catch {}
    }
    await fs.writeFile(srcPath, JSON.stringify(sources, null, 1));
    console.log(`${job.id}: web ${n} images for "${job.query}"`);
  } catch (err) {
    console.log(`${job.id}: web search failed — ${err.message}`);
  }
}
