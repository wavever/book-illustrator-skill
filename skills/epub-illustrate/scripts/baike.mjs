// Appended to "const JOBS = [...]" by find_images.py and run with `ego-browser nodejs`.
// For each job: open the Baidu Baike entry (picking the sense that matches `hint` on
// disambiguation pages), collect its content images at original size, save them as bk_N.jpg
// and append to SOURCE.json, together with the entry's summary for identity checking.
const fs = await import("node:fs/promises");
const task = await taskSpace("epub illustrate image search");
const page = task.page("p1");

for (const job of JOBS) {
  try {
    await page.goto("https://baike.baidu.com/item/" + encodeURIComponent(job.item));
    await page.waitForLoadState();
    await page.waitForTimeout(800);
    const senses = await page.evaluate(() => {
      if (!document.body.innerText.includes("多义词")) return [];
      return [...document.querySelectorAll("a[href*='/item/']")]
        .map((a) => ({ text: a.innerText.trim(), href: a.href }))
        .filter((s) => s.text && s.text.length < 60);
    });
    if (senses.length) {
      const pick = senses.find((s) => job.hint && s.text.includes(job.hint)) || senses[0];
      await page.goto(pick.href);
      await page.waitForLoadState();
      await page.waitForTimeout(800);
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
        if (img.naturalWidth && img.naturalWidth < 100) return;
        const clean = src.split("?")[0];
        if (!urls.includes(clean)) urls.push(clean);
      };
      document.querySelectorAll("[class*='abstractAlbum'] img").forEach(take);
      document.querySelectorAll("a[href*='lemma_content-image'] img, [class*='lemmaPicture'] img").forEach(take);
      document.querySelectorAll("[class*='contentWrapper'] img").forEach(take);
      const summary = (document.querySelector("[class*='lemmaSummary'], .lemma-summary")?.innerText || "")
        .replace(/\s+/g, " ").slice(0, 300);
      return { url: location.href, title: document.title, summary, urls: urls.slice(0, 8) };
    });
    const srcPath = job.dir + "/SOURCE.json";
    const sources = JSON.parse(await fs.readFile(srcPath, "utf-8").catch(() => "[]"));
    let n = 0;
    for (const u of info.urls) {
      const res = await fetch(u, { headers: { Referer: "https://baike.baidu.com/" } });
      if (!res.ok) continue;
      const buf = Buffer.from(await res.arrayBuffer());
      if (buf.length < 8000) continue;
      n += 1;
      const file = `bk_${n}.jpg`;
      await fs.writeFile(`${job.dir}/${file}`, buf);
      sources.push({ file, from: "baike", url: info.url, about: info.summary, img: u });
    }
    await fs.writeFile(srcPath, JSON.stringify(sources, null, 1));
    console.log(`${job.id}: baike ${n} images — ${info.title}`);
  } catch (err) {
    console.log(`${job.id}: baike failed — ${err.message}`);
  }
}
