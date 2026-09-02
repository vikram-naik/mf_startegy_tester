#!/usr/bin/env node
/** Capture every discoverable CAMS IDCW option through its public browser UI.
 *
 * CAMS encrypts the API response used by this page. This collector therefore uses
 * Chrome's DevTools protocol, lets the official application decrypt/render it, and
 * stores the resulting DOM as the immutable per-scheme source payload.
 */

import { spawn } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const LANDING_URL = "https://www.camsonline.com/InvestorServices/COL_ISNAV.aspx";

async function main() {
const args = parseArgs(process.argv.slice(2));
fs.mkdirSync(path.dirname(path.resolve(args.output)), { recursive: true });
const completed = readCompleted(args.output);
const retainedFailures = readRetainedFailures(args.output, completed);
if (args.inspectState) {
  process.stdout.write(`${JSON.stringify({
    event: "cams_capture_state",
    completed: completed.size,
    retained_failures: retainedFailures.size,
    scheme_failure_action: "skip_fund",
    fund_failure_action: "stop_capture",
  })}\n`);
  return;
}
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mfst-cams-chrome-"));
const chrome = launchChrome(profile);

try {
  const browserWs = await chrome.webSocketUrl;
  const client = await CdpClient.connect(browserWs);
  const { targetId } = await client.call("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await client.call("Target.attachToTarget", {
    targetId,
    flatten: true,
  });
  await client.call("Page.enable", {}, sessionId);
  await client.call("Runtime.enable", {}, sessionId);

  await openSelectorPage(client, sessionId);
  const funds = await angularItems(client, sessionId, 0);
  if (!funds.length) {
    const diagnostic = await evaluate(client, sessionId, `({
      url: location.href,
      text: (document.body?.innerText || '').slice(0, 2000),
      ng_selects: document.querySelectorAll('ng-select').length,
      app_html: (document.querySelector('app-root')?.innerHTML || '').slice(0, 2000),
    })`);
    throw new Error(`CAMS fund selector exposed no items: ${JSON.stringify(diagnostic)}`);
  }
  process.stderr.write(`${JSON.stringify({
    event: "cams_fund_inventory_discovered",
    funds: funds.length,
  })}\n`);
  const initialFundCode = itemCode(funds[0], ["MUTUAL_FUND_ID", "MF_CODE", "FUND_CODE", "CODE"]);

  let captured = 0;
  let skipped = 0;
  let deferred = 0;
  let failed = 0;
  let fundsFailed = 0;
  let fundsLoaded = 0;
  let fundSchemeCircuits = 0;
  let eligibleSchemes = 0;
  let identifiedFunds = 0;
  let consecutiveFundFailures = 0;
  let stoppedEarly = false;
  fundLoop: for (const fund of funds) {
    const fundCode = itemCode(fund, ["MUTUAL_FUND_ID", "MF_CODE", "FUND_CODE", "CODE"]);
    const fundName = itemName(fund, ["MUTUAL_FUND_NAME", "MF_NAME", "FUND_NAME", "NAME"]);
    if (!fundCode || !fundName) continue;
    identifiedFunds += 1;
    if (args.fundCodes.length && !args.fundCodes.includes(fundCode)) continue;
    if (args.fundNames.length && !args.fundNames.some(name => fundName.toLowerCase().includes(name.toLowerCase()))) continue;

    let schemes;
    try {
      await openSelectorPage(client, sessionId);
      await selectFundAndLoadSchemes(
        client,
        sessionId,
        fund,
        fundName,
        fundCode !== initialFundCode,
      );
      schemes = await angularItems(client, sessionId, 1);
      fundsLoaded += 1;
      consecutiveFundFailures = 0;
    } catch (error) {
      fundsFailed += 1;
      consecutiveFundFailures += 1;
      appendJsonl(`${args.output}.fund-errors.jsonl`, {
        schema_version: 1,
        provider: "cams",
        captured_at: new Date().toISOString(),
        fund: { code: fundCode, name: fundName },
        source_url: LANDING_URL,
        failure_type: error?.constructor?.name || "Error",
        error: String(error?.message || error),
      });
      process.stderr.write(`${JSON.stringify({
        event: "cams_fund_capture_failed",
        fund_code: fundCode,
        fund_name: fundName,
        error: String(error?.message || error),
      })}\n`);
      if (consecutiveFundFailures >= args.maxConsecutiveFailures) {
        stoppedEarly = true;
        process.stderr.write(`${JSON.stringify({
          event: "cams_capture_circuit_open",
          consecutive_failures: consecutiveFundFailures,
          failure_scope: "fund",
          message: "stopping CAMS capture after consecutive fund-selector failures",
        })}\n`);
        break fundLoop;
      }
      continue;
    }

    const idcwSchemes = schemes.filter(isIdcwScheme);
    eligibleSchemes += idcwSchemes.length;
    process.stderr.write(`${JSON.stringify({
      event: "cams_fund_inventory_loaded",
      fund_code: fundCode,
      fund_name: fundName,
      idcw_schemes: idcwSchemes.length,
    })}\n`);

    let consecutiveSchemeFailures = 0;
    for (const scheme of idcwSchemes) {
      const schemeCode = itemCode(scheme, ["SCHEME_CODE", "CODE"]);
      const schemeName = itemName(scheme, ["SCHEME_NAME", "SHORT_NAME", "NAME"]);
      if (!schemeCode || !schemeName) continue;
      if (args.schemeCodes.length && !args.schemeCodes.includes(schemeCode)) continue;
      if (args.schemeNames.length && !args.schemeNames.some(name => schemeName.toLowerCase().includes(name.toLowerCase()))) continue;
      const key = `${fundCode}\u0000${schemeCode}`;
      if (completed.has(key)) {
        skipped += 1;
        continue;
      }
      if (retainedFailures.has(key) && !args.retryRetainedFailures) {
        deferred += 1;
        continue;
      }
      try {
      await openSelectorPage(client, sessionId);
      await selectFundAndLoadSchemes(
        client,
        sessionId,
        fund,
        fundName,
        fundCode !== initialFundCode,
      );
      await setAngularSelect(client, sessionId, 1, scheme);
      await clickByText(client, sessionId, "submit");
      await waitFor(client, sessionId, async () => {
        const text = await evaluate(client, sessionId, "document.body?.innerText || ''");
        return /SCHEME\s*(CODE|NAME)/i.test(text);
      });
      await clickIdcwTab(client, sessionId);
      await delay(600);
      const rendered = await extractRenderedPage(client, sessionId);
      const records = parseCamsRows(rendered.tables);
      const nav = parseLatestNav(rendered.tables, rendered.text);
      const sourcePayload = JSON.stringify({
        rendered_url: rendered.url,
        rendered_html: rendered.html,
        extracted_tables: rendered.tables,
      });
      const document = {
        schema_version: 1,
        provider: "cams",
        source_url: rendered.url.startsWith("https://www.camsonline.com/") ? rendered.url : LANDING_URL,
        captured_at: new Date().toISOString(),
        fund: { code: fundCode, name: fundName },
        scheme: {
          code: schemeCode,
          name: schemeName,
          plan_type: classifyPlan(schemeName),
          option_variant: classifyVariant(scheme),
          latest_nav_date: nav.date,
          latest_nav_value: nav.value,
        },
        source_payload_sha256: crypto.createHash("sha256").update(sourcePayload, "utf8").digest("hex"),
        source_payload: sourcePayload,
        records,
      };
      appendJsonl(args.output, document);
      captured += 1;
      consecutiveSchemeFailures = 0;
      process.stderr.write(`${JSON.stringify({ event: "cams_scheme_captured", fund_code: fundCode, scheme_code: schemeCode, records: records.length })}\n`);
      if (args.delayMs) await delay(args.delayMs);
      } catch (error) {
        failed += 1;
        consecutiveSchemeFailures += 1;
        appendJsonl(`${args.output}.errors.jsonl`, {
          schema_version: 1,
          provider: "cams",
          captured_at: new Date().toISOString(),
          fund: { code: fundCode, name: fundName },
          scheme: { code: schemeCode, name: schemeName },
          source_url: LANDING_URL,
          failure_type: error?.constructor?.name || "Error",
          error: String(error?.message || error),
        });
        process.stderr.write(`${JSON.stringify({ event: "cams_scheme_capture_failed", fund_code: fundCode, scheme_code: schemeCode, error: String(error?.message || error) })}\n`);
        if (consecutiveSchemeFailures >= args.maxConsecutiveFailures) {
          fundSchemeCircuits += 1;
          process.stderr.write(`${JSON.stringify({
            event: "cams_fund_scheme_circuit_open",
            fund_code: fundCode,
            fund_name: fundName,
            consecutive_failures: consecutiveSchemeFailures,
            message: "skipping the remaining schemes for this fund and continuing CAMS breadth capture",
          })}\n`);
          break;
        }
      }
      if (args.maxSchemes !== null && captured + failed >= args.maxSchemes) break;
    }
    if (args.maxSchemes !== null && captured + failed >= args.maxSchemes) break;
  }
  if (!identifiedFunds) throw new Error("CAMS fund inventory has no recognized code/name fields");
  emitSummary(
    captured,
    skipped,
    deferred,
    failed,
    stoppedEarly,
    funds.length,
    fundsLoaded,
    fundsFailed,
    fundSchemeCircuits,
    eligibleSchemes,
  );
  if (failed || fundsFailed) process.exitCode = 1;
  client.close();
} finally {
  chrome.process.kill("SIGTERM");
  await Promise.race([
    new Promise(resolve => chrome.process.once("exit", resolve)),
    delay(3000),
  ]);
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      fs.rmSync(profile, { recursive: true, force: true });
      break;
    } catch (error) {
      if (attempt === 5) {
        process.stderr.write(`${JSON.stringify({ event: "cams_profile_cleanup_failed", error: error.message })}\n`);
      } else {
        await delay(250);
      }
    }
  }
}
}

function parseArgs(values) {
  const result = {
    output: null,
    fundCodes: [],
    fundNames: [],
    schemeCodes: [],
    schemeNames: [],
    maxSchemes: null,
    delayMs: 250,
    maxConsecutiveFailures: 5,
    retryRetainedFailures: false,
    inspectState: false,
  };
  for (let index = 0; index < values.length; index += 1) {
    const value = values[index];
    if (value === "--output") result.output = values[++index];
    else if (value === "--fund-code") result.fundCodes.push(values[++index]);
    else if (value === "--fund-name") result.fundNames.push(values[++index]);
    else if (value === "--scheme-code") result.schemeCodes.push(values[++index]);
    else if (value === "--scheme-name") result.schemeNames.push(values[++index]);
    else if (value === "--max-schemes") result.maxSchemes = Number(values[++index]);
    else if (value === "--delay-ms") result.delayMs = Number(values[++index]);
    else if (value === "--max-consecutive-failures") result.maxConsecutiveFailures = Number(values[++index]);
    else if (value === "--retry-retained-failures") result.retryRetainedFailures = true;
    else if (value === "--inspect-state") result.inspectState = true;
    else throw new Error(`unknown argument: ${value}`);
  }
  if (!result.output) throw new Error("--output is required");
  if (result.maxSchemes !== null && (!Number.isInteger(result.maxSchemes) || result.maxSchemes < 1)) throw new Error("--max-schemes must be positive");
  if (!Number.isFinite(result.delayMs) || result.delayMs < 0) throw new Error("--delay-ms must be non-negative");
  if (!Number.isInteger(result.maxConsecutiveFailures) || result.maxConsecutiveFailures < 1) throw new Error("--max-consecutive-failures must be positive");
  return result;
}

function launchChrome(profile) {
  const executable = process.env.CHROME_BIN || "google-chrome";
  const child = spawn(executable, [
    "--headless=new",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-debugging-port=0",
    `--user-data-dir=${profile}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  let buffer = "";
  const webSocketUrl = new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Chrome did not expose a DevTools endpoint")), 20000);
    child.stderr.setEncoding("utf8");
    child.stderr.on("data", chunk => {
      buffer += chunk;
      const match = buffer.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) {
        clearTimeout(timer);
        resolve(match[1]);
      }
    });
    child.once("exit", code => {
      clearTimeout(timer);
      reject(new Error(`Chrome exited before startup (code ${code}): ${buffer.slice(-1000)}`));
    });
  });
  return { process: child, webSocketUrl };
}

class CdpClient {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    socket.addEventListener("message", event => {
      const message = JSON.parse(event.data);
      if (!message.id) return;
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
      else pending.resolve(message.result || {});
    });
  }

  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", reject, { once: true });
    });
    return new CdpClient(socket);
  }

  call(method, params = {}, sessionId = undefined) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method}: DevTools response timed out`));
      }, 30000);
      this.pending.set(id, {
        resolve: value => { clearTimeout(timer); resolve(value); },
        reject: error => { clearTimeout(timer); reject(error); },
        method,
      });
      this.socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }

  close() { this.socket.close(); }
}

async function navigate(client, sessionId, url) {
  await client.call("Page.navigate", { url }, sessionId);
  await waitFor(client, sessionId, async () => await evaluate(client, sessionId, "document.readyState === 'complete'"), 30000);
  await delay(800);
}

async function dismissOverlays(client, sessionId) {
  await evaluate(client, sessionId, `(() => {
    const terms = ['accept', 'agree', 'continue', 'close', 'skip'];
    for (const element of [...document.querySelectorAll('button, a, input[type=button]')]) {
      const text = (element.innerText || element.value || '').trim().toLowerCase();
      if (terms.some(term => text === term || text.startsWith(term + ' '))) element.click();
    }
    return true;
  })()`);
  await delay(400);
}

async function openSelectorPage(client, sessionId) {
  await navigate(client, sessionId, LANDING_URL);
  await dismissOverlays(client, sessionId);
  await waitFor(
    client,
    sessionId,
    async () => await evaluate(
      client,
      sessionId,
      "[...document.querySelectorAll('u, td')].some(element => /Mutual Fund$/i.test((element.innerText || '').trim()))",
    ),
    30000,
  );
  const clicked = await evaluate(client, sessionId, `(() => {
    const leaf = [...document.querySelectorAll('u, td, a, button, span, div')].find(element => {
      const text = (element.innerText || '').replace(/\\s+/g, ' ').trim();
      return /Mutual Fund$/i.test(text) && text.length < 100 && element.children.length === 0;
    });
    if (!leaf) return false;
    leaf.click();
    return true;
  })()`);
  if (!clicked) {
    const candidates = await evaluate(client, sessionId, `
      [...document.querySelectorAll('*')]
        .filter(element => /HDFC Mutual Fund/i.test(element.innerText || ''))
        .slice(-8)
        .map(element => ({
          tag: element.tagName,
          class_name: element.className,
          text: (element.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 150),
        }))
    `);
    throw new Error(`CAMS page exposes no clickable mutual-fund row: ${JSON.stringify(candidates)}`);
  }
  await waitFor(
    client,
    sessionId,
    async () => await evaluate(client, sessionId, "document.querySelectorAll('ng-select').length > 0"),
    30000,
  );
  await delay(500);
}

async function angularItems(client, sessionId, selectIndex) {
  return await evaluate(client, sessionId, `(() => {
    const element = document.querySelectorAll('ng-select')[${selectIndex}];
    if (!element || !element.__ngContext__) return [];
    const visit = value => {
      if (!value || typeof value !== 'object') return null;
      if (Array.isArray(value._items)) return value._items;
      return null;
    };
    for (const value of element.__ngContext__) {
      const found = visit(value);
      if (found) return JSON.parse(JSON.stringify(found));
    }
    return [];
  })()`);
}

async function setAngularSelect(client, sessionId, index, item) {
  const serialized = JSON.stringify(item);
  const set = await evaluate(client, sessionId, `(() => {
    const element = document.querySelectorAll('ng-select')[${index}];
    if (!element || !element.__ngContext__) return false;
    const item = ${serialized};
    const bindValue = element.getAttribute('bindvalue') || element.getAttribute('bindValue');
    const selected = bindValue && Object.hasOwn(item, bindValue) ? item[bindValue] : item;
    for (const value of element.__ngContext__) {
      if (value && value.control && !value.control.controls && typeof value.control.setValue === 'function') {
        value.control.setValue(selected);
        value.control.markAsDirty?.();
        value.control.updateValueAndValidity?.();
        element.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
      }
    }
    return false;
  })()`);
  if (!set) throw new Error(`unable to set CAMS selector ${index}`);
  await delay(700);
}

async function selectFundAndLoadSchemes(client, sessionId, fund, fundName, mustChange) {
  const before = (await angularItems(client, sessionId, 1))
    .map(item => itemCode(item, ["SCHEME_CODE", "CODE"]))
    .join("\u0000");
  if (mustChange) await selectVisibleOption(client, sessionId, 0, fundName);
  else await setAngularSelect(client, sessionId, 0, fund);
  await waitFor(client, sessionId, async () => {
    const schemes = await angularItems(client, sessionId, 1);
    if (!schemes.length) return false;
    const after = schemes
      .map(item => itemCode(item, ["SCHEME_CODE", "CODE"]))
      .join("\u0000");
    return !mustChange || after !== before;
  }, 30000);
}

async function selectVisibleOption(client, sessionId, index, optionText) {
  const prepared = await evaluate(client, sessionId, `(() => {
    const element = document.querySelectorAll('ng-select')[${index}];
    if (!element) return false;
    element.click();
    const input = element.querySelector('input');
    if (input) {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;
      setter.call(input, ${JSON.stringify(optionText)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    return true;
  })()`);
  if (!prepared) throw new Error(`unable to open CAMS selector ${index}`);
  await waitFor(client, sessionId, async () => await evaluate(client, sessionId, `
    [...document.querySelectorAll('.ng-option')].some(option =>
      (option.innerText || '').replace(/\\s+/g, ' ').trim() === ${JSON.stringify(optionText)}
    )
  `));
  const clicked = await evaluate(client, sessionId, `(() => {
    const target = [...document.querySelectorAll('.ng-option')].find(option =>
      (option.innerText || '').replace(/\\s+/g, ' ').trim() === ${JSON.stringify(optionText)}
    );
    if (!target) return false;
    target.click();
    return true;
  })()`);
  if (!clicked) throw new Error(`unable to select CAMS option ${optionText}`);
  await delay(500);
}

async function clickByText(client, sessionId, needle) {
  const clicked = await evaluate(client, sessionId, `(() => {
    const needle = ${JSON.stringify(needle.toLowerCase())};
    const element = [...document.querySelectorAll('button, input[type=submit], a')]
      .find(item => ((item.innerText || item.value || '').trim().toLowerCase()).includes(needle));
    if (!element) return false;
    element.click();
    return true;
  })()`);
  if (!clicked) throw new Error(`CAMS page has no control containing ${needle}`);
}

async function clickIdcwTab(client, sessionId) {
  await waitFor(client, sessionId, async () => await evaluate(client, sessionId, `
    [...document.querySelectorAll('[role=tab], .mat-tab-label')]
      .some(item => /IDCW|DIVIDEND/i.test(item.innerText || ''))
  `), 30000);
  const clicked = await evaluate(client, sessionId, `(() => {
    const tabs = [...document.querySelectorAll('[role=tab], .mat-tab-label')];
    const element = tabs.find(item => /IDCW|DIVIDEND/i.test(item.innerText || ''))
      || [...document.querySelectorAll('button, a')]
        .find(item => /(?:IDCW|DIVIDEND)\s*(?:HISTORY|DETAILS)/i.test(item.innerText || ''));
    if (!element) return false;
    element.click();
    return true;
  })()`);
  if (!clicked) throw new Error("CAMS detail page has no IDCW/dividend tab");
}

async function extractRenderedPage(client, sessionId) {
  return await evaluate(client, sessionId, `(() => ({
    url: location.href,
    text: document.body?.innerText || '',
    html: document.documentElement.outerHTML,
    tables: [...document.querySelectorAll('table')].map(table =>
      [...table.querySelectorAll('tr')].map(row =>
        [...row.querySelectorAll('th,td')].map(cell => (cell.innerText || '').replace(/\\s+/g, ' ').trim())
      )
    ),
  }))()`);
}

function parseCamsRows(tables) {
  for (const table of tables) {
    const headerIndex = table.findIndex(row => {
      const header = row.map(normalizeHeader);
      return header.some(value => value === "idcw date" || value === "dividend date")
        && header.some(value => value.endsWith("retail"));
    });
    if (headerIndex < 0) continue;
    const header = table[headerIndex].map(normalizeHeader);
    const dateIndex = header.findIndex(value => value === "idcw date" || value === "dividend date");
    const retailIndex = header.findIndex(value => value.endsWith("retail"));
    const corporateIndex = header.findIndex(value => value.endsWith("corporate"));
    const rows = [];
    const seen = new Set();
    for (const source of table.slice(headerIndex + 1)) {
      if (source.length !== header.length) throw new Error("CAMS IDCW row has an unexpected column count");
      const recordDate = parseDate(source[dateIndex]);
      if (seen.has(recordDate)) throw new Error(`duplicate CAMS IDCW date ${recordDate}`);
      seen.add(recordDate);
      rows.push({
        record_date: recordDate,
        individual_amount: positiveDecimal(source[retailIndex], "retail amount"),
        non_individual_amount: corporateIndex >= 0 ? optionalNonNegativeDecimal(source[corporateIndex], "corporate amount") : null,
        ex_nav: null,
        cum_nav: null,
        source_terminology: "CAMS IDCW history",
      });
    }
    return rows;
  }
  // CAMS displays an empty tab without a data table for options with no payouts.
  return [];
}

function parseLatestNav(tables, text) {
  const flatRows = tables.flat();
  for (const row of flatRows) {
    for (let index = 0; index < row.length; index += 1) {
      const label = normalizeHeader(row[index]);
      if (label.includes("nav date") && row[index + 1]) {
        const dateValue = tryDate(row[index]) || tryDate(row[index + 1]);
        const amount = findNearbyDecimal(row, index + 1);
        if (dateValue && amount) return { date: dateValue, value: amount };
      }
    }
  }
  const match = text.match(/(?:NAV\s*DATE|AS\s*ON)\s*[:\-]?\s*(\d{1,2}[\/-]\d{1,2}[\/-]\d{4})[\s\S]{0,100}?NAV(?:\s*VALUE)?\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)/i);
  if (match) return { date: parseDate(match[1]), value: positiveDecimal(match[2], "latest NAV") };
  return { date: null, value: null };
}

function findNearbyDecimal(row, start) {
  for (const value of row.slice(start, start + 4)) {
    const match = value.replaceAll(",", "").match(/(?:^|\s)([0-9]+(?:\.[0-9]+)?)(?:\s|$)/);
    if (match && Number(match[1]) > 0) return String(Number(match[1]));
  }
  return null;
}

function itemCode(item, keys) {
  for (const key of keys) if (item[key] !== undefined && item[key] !== null) return String(item[key]).trim();
  const fallback = Object.keys(item).find(key => /(?:^|_)(?:MF|FUND)?_?(?:ID|CODE)$/i.test(key));
  return fallback && item[fallback] !== undefined && item[fallback] !== null ? String(item[fallback]).trim() : "";
}
function itemName(item, keys) {
  for (const key of keys) if (item[key]) return String(item[key]).trim();
  const fallback = Object.keys(item).find(key => /(?:^|_)(?:MF|FUND|SCHEME)?_?NAME$/i.test(key));
  return fallback && item[fallback] ? String(item[fallback]).trim() : "";
}
function isIdcwScheme(item) {
  const name = itemName(item, ["SCHEME_NAME", "SHORT_NAME", "NAME"]);
  const reinvest = String(item.REINVEST || "").toUpperCase();
  return /IDCW|DIVIDEND/i.test(name) || ["X", "Y"].includes(reinvest);
}
function classifyPlan(name) { if (/DIRECT/i.test(name)) return "direct"; if (/REGULAR|RETAIL|INSTITUTIONAL/i.test(name)) return "regular"; return "unknown"; }
function classifyVariant(item) {
  const name = itemName(item, ["SCHEME_NAME", "SHORT_NAME", "NAME"]);
  const reinvest = String(item.REINVEST || "").toUpperCase();
  if (/REINVEST/i.test(name) || reinvest === "Y") return "reinvestment";
  if (/PAYOUT|IDCW|DIVIDEND/i.test(name) || reinvest === "X") return "payout";
  return "unknown";
}
function normalizeHeader(value) { return value.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim(); }
function parseDate(value) {
  const parsed = tryDate(value);
  if (!parsed) throw new Error(`invalid CAMS date: ${JSON.stringify(value)}`);
  return parsed;
}
function tryDate(value) {
  const normalized = String(value).trim();
  const numeric = normalized.match(/(\d{1,2})[\/-](\d{1,2})[\/-](\d{4})/);
  const named = normalized.match(/(\d{1,2})-([A-Za-z]{3})-(\d{4})/);
  let day;
  let month;
  let year;
  if (numeric) {
    [, day, month, year] = numeric;
  } else if (named) {
    const months = { jan: "01", feb: "02", mar: "03", apr: "04", may: "05", jun: "06", jul: "07", aug: "08", sep: "09", oct: "10", nov: "11", dec: "12" };
    day = named[1];
    month = months[named[2].toLowerCase()];
    year = named[3];
    if (!month) return null;
  } else {
    return null;
  }
  const iso = `${year}-${month.padStart(2, "0")}-${day.padStart(2, "0")}`;
  const date = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(date.valueOf()) ? null : iso;
}
function positiveDecimal(value, label) {
  const normalized = String(value).replaceAll(",", "").trim();
  if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(normalized) || Number(normalized) <= 0) throw new Error(`invalid CAMS ${label}: ${JSON.stringify(value)}`);
  return normalized;
}
function optionalNonNegativeDecimal(value, label) {
  const normalized = String(value || "").trim();
  if (!normalized || ["-", "--", "NA", "N/A"].includes(normalized.toUpperCase())) return null;
  if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(normalized)) throw new Error(`invalid CAMS ${label}: ${JSON.stringify(value)}`);
  return normalized;
}

async function evaluate(client, sessionId, expression) {
  const result = await client.call("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true }, sessionId);
  if (result.exceptionDetails) {
    const description = result.exceptionDetails.exception?.description || result.exceptionDetails.text;
    throw new Error(`browser evaluation failed: ${description}`);
  }
  return result.result.value;
}
async function waitFor(client, sessionId, predicate, timeoutMs = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await predicate()) return;
    await delay(250);
  }
  throw new Error("timed out waiting for the CAMS page");
}
function delay(milliseconds) { return new Promise(resolve => setTimeout(resolve, milliseconds)); }
function appendJsonl(output, value) { fs.appendFileSync(output, `${JSON.stringify(value)}\n`, { encoding: "utf8", flush: true }); }
function readCompleted(output) {
  if (!fs.existsSync(output)) return new Set();
  const result = new Set();
  for (const [index, line] of fs.readFileSync(output, "utf8").split(/\r?\n/).entries()) {
    if (!line.trim()) continue;
    try {
      const value = JSON.parse(line);
      result.add(`${value.fund.code}\u0000${value.scheme.code}`);
    } catch (error) {
      throw new Error(`existing CAMS capture line ${index + 1} is invalid: ${error.message}`);
    }
  }
  return result;
}
function readRetainedFailures(output, completed) {
  const errorOutput = `${output}.errors.jsonl`;
  if (!fs.existsSync(errorOutput)) return new Set();
  const result = new Set();
  for (const [index, line] of fs.readFileSync(errorOutput, "utf8").split(/\r?\n/).entries()) {
    if (!line.trim()) continue;
    try {
      const value = JSON.parse(line);
      if (value.provider !== "cams" || !value.fund?.code || !value.scheme?.code) {
        throw new Error("unexpected provider or identity shape");
      }
      const key = `${value.fund.code}\u0000${value.scheme.code}`;
      if (!completed.has(key)) result.add(key);
    } catch (error) {
      throw new Error(`existing CAMS error line ${index + 1} is invalid: ${error.message}`);
    }
  }
  return result;
}
function emitSummary(
  captured,
  skipped,
  deferred,
  failed,
  stoppedEarly,
  fundsDiscovered,
  fundsLoaded,
  fundsFailed,
  fundSchemeCircuits,
  eligibleSchemes,
) {
  process.stderr.write(`${JSON.stringify({
    event: "cams_capture_completed",
    captured,
    resumed_skipped: skipped,
    retained_failures_deferred: deferred,
    failed,
    stopped_early: stoppedEarly,
    funds_discovered: fundsDiscovered,
    funds_loaded: fundsLoaded,
    funds_failed: fundsFailed,
    fund_scheme_circuits: fundSchemeCircuits,
    eligible_schemes: eligibleSchemes,
  })}\n`);
}

await main();
