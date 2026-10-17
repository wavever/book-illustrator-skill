// Legacy, opt-in ("sources" lists "web", "browser": "ego", "web_engine": "bing"). Appended to
// "const JOBS = [...]" by find_images.py and run with `ego-browser nodejs`. Runs a Bing Images
// search for each of job.queries and writes {status, hits: [{img, page, about, query}]} to job.out;
// find_images.py then downloads and validates the images itself.
const fs = await import("node:fs/promises");
const task = await taskSpace("book illustrator image search");
const page = task.page("p1");

for (const job of JOBS) {
  const hits = [];
  const errors = [];
  for (const query of job.queries) {
    try {
      await page.goto("https://cn.bing.com/images/search?q=" + encodeURIComponent(query) + "&first=1");
      await page.waitForLoadState();
      await page.waitForTimeout(1500);
      const found = await page.evaluate((limit) => {
        const out = [];
        for (const a of document.querySelectorAll("a.iusc")) {
          try {
            const m = JSON.parse(a.getAttribute("m"));
            if (m.murl) out.push({ img: m.murl, page: m.purl || "", about: m.t || "" });
          } catch {}
          if (out.length >= limit) break;
        }
        return out;
      }, job.limit || 8);
      for (const h of found) hits.push({ ...h, query });
      console.log(`${job.id}: web ${found.length} image URLs for "${query}"`);
    } catch (err) {
      errors.push(String(err && err.message || err));
      console.log(`${job.id}: web search failed — ${err.message}`);
    }
  }
  const status = hits.length || !errors.length ? "ok" : "failed";
  await fs.writeFile(job.out, JSON.stringify({ status, hits, error: errors.join("; ") }, null, 1));
}
