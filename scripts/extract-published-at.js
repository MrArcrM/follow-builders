// ============================================================================
// Local publishedAt enrichment helper
// ============================================================================
// Used by prepare-digest.js to fix the upstream feed's missing publishedAt
// values without relying on upstream re-running their GHA. We pay the cost
// of one extra HTTP fetch per blog with a missing date — typically 0-3 per
// digest run.
//
// Source-of-truth ordering (most reliable first):
//   1. <meta property="article:published_time" content="...">  (OpenGraph)
//   2. <meta name="article:published_time" / itemprop="datePublished" ...>
//   3. <time datetime="..."> inside <article> (or first one in page)
//   4. JSON-LD: recursively walk all ld+json blocks and any @graph arrays
//      looking for datePublished / dateCreated, regardless of @type shape
//   5. Visible "Published <Mon DD, YYYY>" text
//   6. Next.js App Router RSC streaming chunk's _createdAt (Anthropic-style)
//
// All values are normalized to ISO 8601 via normalizeDate. Never throws —
// callers can assume null on any failure (network, parse, no metadata).
// ============================================================================

const FETCH_TIMEOUT_MS = 8000;
const FETCH_USER_AGENT =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

export function extractPublishedAtFromHtml(html) {
  if (!html) return null;

  const ogMatch =
    html.match(
      /<meta[^>]+property=["']article:published_time["'][^>]+content=["']([^"']+)["']/i,
    ) ||
    html.match(
      /<meta[^>]+content=["']([^"']+)["'][^>]+property=["']article:published_time["']/i,
    );
  if (ogMatch) return normalizeDate(ogMatch[1]);

  const itempropMatch =
    html.match(
      /<meta[^>]+itemprop=["']datePublished["'][^>]+content=["']([^"']+)["']/i,
    ) ||
    html.match(
      /<meta[^>]+name=["']article:published_time["'][^>]+content=["']([^"']+)["']/i,
    ) ||
    html.match(
      /<meta[^>]+name=["']pubdate["'][^>]+content=["']([^"']+)["']/i,
    );
  if (itempropMatch) return normalizeDate(itempropMatch[1]);

  const articleMatch = html.match(/<article[^>]*>([\s\S]*?)<\/article>/i);
  const articleScope = articleMatch ? articleMatch[1] : html;
  const timeMatch = articleScope.match(/<time[^>]+datetime=["']([^"']+)["']/i);
  if (timeMatch) return normalizeDate(timeMatch[1]);

  const jsonLdRegex =
    /<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  let ldMatch;
  while ((ldMatch = jsonLdRegex.exec(html)) !== null) {
    try {
      const parsed = JSON.parse(ldMatch[1]);
      const found = findDatePublished(parsed);
      if (found) return normalizeDate(found);
    } catch {
      // Not valid JSON, skip
    }
  }

  // Allow zero or more HTML tags / comments between "Published" and the date.
  // Anthropic renders this as `Published <!-- -->Apr 23, 2026` (the comment is
  // a React SSR boundary marker), so a single-tag-only pattern misses it.
  const publishedTextMatch = html.match(
    /Published[\s:]*(?:<[^>]*>\s*)*([A-Z][a-z]{2,8}\s+\d{1,2},?\s+\d{4})/,
  );
  if (publishedTextMatch) return normalizeDate(publishedTextMatch[1]);

  // Anthropic embeds Sanity article data in self.__next_f.push() chunks.
  // The first _createdAt in the page belongs to the article itself; image
  // children appear later. Acceptable risk for a last-resort fallback.
  const rscMatch = html.match(
    /"_createdAt"\s*:\s*"(\d{4}-\d{2}-\d{2}T[^"]+)"/,
  );
  if (rscMatch) return normalizeDate(rscMatch[1]);

  return null;
}

export function findArticleNode(node) {
  if (!node) return null;
  if (Array.isArray(node)) {
    for (const item of node) {
      const hit = findArticleNode(item);
      if (hit) return hit;
    }
    return null;
  }
  if (typeof node !== "object") return null;
  const type = node["@type"];
  const types = Array.isArray(type) ? type : type ? [type] : [];
  const isArticleType = types.some((t) =>
    /^(Blog)?(Posting|Article|NewsArticle|TechArticle)$/i.test(t),
  );
  if (isArticleType && (node.datePublished || node.headline)) return node;
  if (node.datePublished && (node.headline || node.name)) return node;
  if (node["@graph"]) {
    const hit = findArticleNode(node["@graph"]);
    if (hit) return hit;
  }
  return null;
}

export function findDatePublished(node) {
  if (!node) return null;
  if (Array.isArray(node)) {
    for (const item of node) {
      const hit = findDatePublished(item);
      if (hit) return hit;
    }
    return null;
  }
  if (typeof node !== "object") return null;
  if (typeof node.datePublished === "string") return node.datePublished;
  if (typeof node.dateCreated === "string") return node.dateCreated;
  if (node["@graph"]) {
    const hit = findDatePublished(node["@graph"]);
    if (hit) return hit;
  }
  return null;
}

export function normalizeDate(raw) {
  if (!raw) return null;
  const trimmed = String(raw).trim();
  const d = new Date(trimmed);
  if (!isNaN(d.getTime())) return d.toISOString();
  return trimmed;
}

// Fetches an article URL and returns its publishedAt as ISO 8601, or null if
// the fetch fails / no date can be found. Bounded by FETCH_TIMEOUT_MS so a
// hung host doesn't block the whole digest run.
export async function fetchPublishedAt(url) {
  if (!url) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: { "User-Agent": FETCH_USER_AGENT },
    });
    if (!res.ok) return null;
    const html = await res.text();
    return extractPublishedAtFromHtml(html);
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
