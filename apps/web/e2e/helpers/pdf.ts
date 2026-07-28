/**
 * PDF text extraction for content assertions.
 *
 * `pdfjs-dist` is a **devDependency** — it is never imported by application
 * code and never reaches the client bundle. Checking magic bytes and file size
 * would only prove a PDF exists; the brief requires proving what is *in* it.
 *
 * No OCR: text is pulled from the content stream, which is exact.
 */

/**
 * `pdfjs-dist` v6 is ESM-only, and Playwright loads specs as CommonJS. A
 * literal `await import(...)` would be rewritten to `require()` by the
 * transform and then fail on an ES module; building the import through
 * `Function` keeps it a genuine dynamic import at runtime.
 */
const importEsm = new Function("specifier", "return import(specifier)") as (
  specifier: string,
) => Promise<PdfjsModule>;

type PdfjsModule = {
  getDocument: (options: Record<string, unknown>) => { promise: Promise<PdfDocument> };
  OPS: Record<string, number>;
};

type PdfDocument = {
  numPages: number;
  getPage: (pageNumber: number) => Promise<PdfPage>;
};

type PdfPage = {
  getTextContent: () => Promise<{ items: { str?: string }[] }>;
  getOperatorList: () => Promise<{ fnArray: number[] }>;
};

let cached: PdfjsModule | null = null;

async function loadPdfjs(): Promise<PdfjsModule> {
  cached ??= await importEsm("pdfjs-dist/legacy/build/pdf.mjs");
  return cached;
}

export type PdfContent = {
  pageCount: number;
  /** Whitespace-normalised text of every page, concatenated. */
  text: string;
  /** True when the document embeds at least one raster image. */
  hasImage: boolean;
  imageCount: number;
};

/** Collapse runs of whitespace so line breaks never break an assertion. */
export function normalise(text: string): string {
  return text.replace(/\s+/g, " ").trim();
}

export async function extractPdf(bytes: Buffer | Uint8Array): Promise<PdfContent> {
  // The legacy build targets older runtimes and avoids the worker entirely,
  // which is what makes it usable in a plain Node process.
  const pdfjs = await loadPdfjs();

  const data = new Uint8Array(bytes);
  const document = await pdfjs.getDocument({
    data,
    useSystemFonts: true,
    verbosity: 0,
  }).promise;

  const parts: string[] = [];
  let imageCount = 0;

  for (let pageNumber = 1; pageNumber <= document.numPages; pageNumber += 1) {
    const page = await document.getPage(pageNumber);

    const content = await page.getTextContent();
    parts.push(content.items.map((item) => item.str ?? "").join(" "));

    // Raster images land in the page's XObject resources.
    try {
      const operators = await page.getOperatorList();
      const paintImage = pdfjs.OPS.paintImageXObject;
      const paintJpeg = pdfjs.OPS.paintJpegXObject;
      for (const op of operators.fnArray) {
        if (op === paintImage || op === paintJpeg) imageCount += 1;
      }
    } catch {
      // Image counting is a bonus signal; text extraction is the contract.
    }
  }

  return {
    pageCount: document.numPages,
    text: normalise(parts.join(" ")),
    hasImage: imageCount > 0,
    imageCount,
  };
}

/** A PDF must at least be a PDF. Cheap guard before parsing. */
export function looksLikePdf(bytes: Buffer | Uint8Array): boolean {
  const header = Buffer.from(bytes.subarray(0, 5)).toString("latin1");
  return header === "%PDF-";
}

/**
 * Currency and number formatting varies between the PDF and the web page, so
 * comparisons strip separators and symbols before matching.
 */
export function numericTokens(text: string): Set<string> {
  const tokens = new Set<string>();
  for (const match of text.matchAll(/\d[\d,\s]*(?:\.\d+)?/g)) {
    const cleaned = match[0].replace(/[,\s]/g, "");
    if (cleaned.length > 0) tokens.add(cleaned);
  }
  return tokens;
}

/** True when `value` appears in `text` ignoring thousands separators. */
export function containsNumber(text: string, value: string | number): boolean {
  const target = String(value).replace(/[,\s]/g, "");
  if (normalise(text).replace(/[,\s]/g, "").includes(target)) return true;
  return numericTokens(text).has(target);
}
