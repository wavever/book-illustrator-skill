// Legacy, opt-in ("sources" lists "baike" and "browser": "ego"). Appended to "const JOBS = [...]"
// by find_images.py and run with `ego-browser nodejs`. For each job: open the Baidu Baike entry
// (job.url if given; on a 多义词 page only the one sense matching job.hint is followed, otherwise the
// job is reported ambiguous), collect its content image URLs and write {status, hits, ...} to
// job.out. find_images.py then downloads and validates the images itself.
const fs = await import("node:fs/promises");
const task = await taskSpace("book illustrator image search");
const page = task.page("p1");

for (const job of JOBS) {
  const write = (obj) => fs.writeFile(job.out, JSON.stringify(obj, null, 1));
  try {
    await page.goto(job.url || "https://baike.baidu.com/item/" + encodeURIComponent(job.item));
    await page.waitForLoadState();
    await page.waitForTimeout(800);
    if (!job.url) {
      const senses = await page.evaluate(() => {
        if (!document.body.innerText.includes("多义词")) return [];
        return [...document.querySelectorAll("a[href*='/item/']")]
          .map((a) => ({ text: a.innerText.trim(), href: a.href }))
          .filter((s) => s.text && s.text.length < 60);
      });
      if (senses.length) {
        const matches = senses.filter((s) => job.hint && s.text.includes(job.hint));
        const hrefs = [...new Set(matches.map((s) => s.href))];
        if (hrefs.length !== 1) {
          await write({ status: "ambiguous", senses: senses.slice(0, 10),
            error: `Baike "${job.item}" has several senses; set "baike_url" on the entity (or a "hint")` });
          console.log(`${job.id}: baike ambiguous`);
          continue;
        }
        await page.goto(hrefs[0]);
        await page.waitForLoadState();
        await page.waitForTimeout(800);
      }
    }
    for (let i = 0; i < 4; i++) {
      await page.evaluate(() => window.scrollBy(0, 1200));
      await page.waitForTimeout(300);
    }
    const info = await page.evaluate(() => {
      // Entry photos only: the summary picture (abstractAlbum) first, then pictures inside the
      // article body. Video covers (with headline overlays) and related-entry thumbnails are skipped.
      const urls = [];
      const take = (img) => {
        const src = img.currentSrc || img.src || img.dataset.src || "";
        if (!/bkimg\.cdn\.bcebos\.com\/pic\//.test(src)) return;
        if (img.closest("[class*='video'], [class*='Video'], [class*='relation'], [class*='Relation']")) return;
        const clean = src.split("?")[0];
        if (!urls.includes(clean)) urls.push(clean);
      };
      document.querySelectorAll("[class*='abstractAlbum'] img").forEach(take);
      document.querySelectorAll("a[href*='lemma_content-image'] img, [class*='lemmaPicture'] img").forEach(take);
      document.querySelectorAll("[class*='contentWrapper'] img").forEach(take);
      const summary = (document.querySelector("[class*='lemmaSummary'], .lemma-summary")?.innerText || "")
        .replace(/\s+/g, " ").slice(0, 300);
      return { url: location.href, title: document.title, summary, urls };
    });
    const about = `${info.title} ${info.summary}`.trim();
    await write({ status: "ok", hits: info.urls.slice(0, job.limit || 8).map((img) => ({ img, page: info.url, about })) });
    console.log(`${job.id}: baike ${info.urls.length} image URLs — ${info.title}`);
  } catch (err) {
    await write({ status: "failed", error: String(err && err.message || err) });
    console.log(`${job.id}: baike failed — ${err.message}`);
  }
}
