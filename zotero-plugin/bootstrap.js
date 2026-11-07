/**
 * SearchTools Evidence Bridge - Zotero 7/8 Plugin (MVP)
 * Connects SearchTools with Zotero:
 * 1. Jumps to exact PDF page & creates temporary evidence highlight with tag #evidence-temp
 * 2. One-click cleanup of all temporary evidence annotations
 * 3. Reverse creation of evidence cards from Reader text selection
 */

var Zotero;
var pollIntervalId = null;

const SEARCHTOOLS_HOST = 'http://127.0.0.1:8765';
const ADDON_ID = 'evidence-bridge@searchtools.org';

function log(msg) {
  dump(`[Evidence Bridge] ${msg}\n`);
  if (typeof Zotero !== 'undefined' && Zotero.debug) {
    Zotero.debug(`[Evidence Bridge] ${msg}`);
  }
  try {
    httpPost(`${SEARCHTOOLS_HOST}/api/zotero/log`, { msg: String(msg) }).catch(() => {});
  } catch (e) {}
}

async function waitForZotero() {
  if (typeof Zotero !== 'undefined' && Zotero.initializationPromise) {
    await Zotero.initializationPromise;
    return Zotero;
  }
  let wm = Services.wm;
  let win = wm.getMostRecentWindow('navigator:browser');
  if (win && win.Zotero) {
    Zotero = win.Zotero;
    await Zotero.initializationPromise;
    return Zotero;
  }
  let windows = wm.getEnumerator('navigator:browser');
  var found = false;
  while (windows.hasMoreElements()) {
    let w = windows.getNext();
    if (w && w.Zotero) {
      Zotero = w.Zotero;
      found = true;
      break;
    }
  }
  if (!found) {
    await new Promise((resolve) => {
      let listener = {
        onOpenWindow(aWindow) {
          let domWindow = aWindow.QueryInterface(Ci.nsIInterfaceRequestor).getInterface(Ci.nsIDOMWindowInternal || Ci.nsIDOMWindow);
          domWindow.addEventListener('load', function onLoad() {
            domWindow.removeEventListener('load', onLoad, false);
            if (domWindow.Zotero) {
              wm.removeListener(listener);
              Zotero = domWindow.Zotero;
              resolve(void 0);
            }
          }, false);
        }
      };
      wm.addListener(listener);
    });
  }
  await Zotero.initializationPromise;
  return Zotero;
}

async function httpGet(url) {
  if (typeof fetch !== 'undefined') {
    let res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  }
  let res = await Zotero.HTTP.request('GET', url, { responseType: 'json' });
  return typeof res.response === 'object' ? res.response : JSON.parse(res.responseText);
}

async function httpPost(url, payload) {
  let headers = {
    'Content-Type': 'application/json',
    'X-SearchTools-Token': 'local-mcp'
  };
  if (typeof fetch !== 'undefined') {
    let res = await fetch(url, {
      method: 'POST',
      headers: headers,
      body: JSON.stringify(payload)
    });
    return await res.json();
  }
  let res = await Zotero.HTTP.request('POST', url, {
    headers: headers,
    body: JSON.stringify(payload),
    responseType: 'json'
  });
  return typeof res.response === 'object' ? res.response : JSON.parse(res.responseText);
}

async function handleCardOpen(cardId) {
  try {
    log(`Handling card open request for: ${cardId}`);
    let url = `${SEARCHTOOLS_HOST}/api/evidence-cards/locate?id=${encodeURIComponent(cardId)}`;
    let data = await httpGet(url);
    await highlightAndOpenCard(data);
  } catch (e) {
    log(`Error in handleCardOpen: ${e}`);
    if (typeof Zotero !== 'undefined' && Zotero.logError) {
      Zotero.logError(e);
    }
  }
}

async function highlightAndOpenCard(cardData) {
  if (!cardData || !cardData.card_id) return;
  await waitForZotero();
  let userLibraryID = Zotero.Libraries.userLibraryID;
  let attKey = cardData.attachment_key;
  let zotKey = cardData.zotero_key;

  let attachment = null;
  if (attKey) {
    attachment = await Zotero.Items.getByLibraryAndKeyAsync(userLibraryID, attKey);
  }
  if (!attachment && zotKey) {
    let parentItem = await Zotero.Items.getByLibraryAndKeyAsync(userLibraryID, zotKey);
    if (parentItem) {
      let attIds = parentItem.getAttachments();
      if (attIds && attIds.length) {
        attachment = await Zotero.Items.getAsync(attIds[0]);
      }
    }
  }

  if (!attachment || !attachment.isFileAttachment()) {
    log(`Attachment item not found for card ${cardData.card_id} (att: ${attKey}, zot: ${zotKey})`);
    return;
  }

  let pageIndex = parseInt(cardData.page_index != null ? cardData.page_index : 0);
  let pageLabel = String(cardData.page_label || (pageIndex + 1));
  let rects = cardData.rects || [];

  // Check if temporary annotation already exists for this card
  let cardTag = '#card-' + cardData.card_id;
  let annots = attachment.getAnnotations ? attachment.getAnnotations() : [];
  let targetAnnot = null;
  for (let a of annots) {
    if (a.hasTag('#evidence-temp') && a.hasTag(cardTag)) {
      targetAnnot = a;
      break;
    }
  }

  if (!targetAnnot && rects.length > 0) {
    try {
      let firstRect = rects[0] || [0, 0, 0, 0];
      let sortIndex = String(pageIndex).padStart(5, '0') + '|' +
        String(Math.max(0, Math.round(1000 - (firstRect[1] || 0)))).padStart(6, '0') + '|' +
        String(Math.max(0, Math.round(firstRect[0] || 0))).padStart(5, '0');

      targetAnnot = new Zotero.Item('annotation');
      targetAnnot.libraryID = attachment.libraryID;
      targetAnnot.parentID = attachment.id;
      targetAnnot.annotationType = 'highlight';
      targetAnnot.annotationText = cardData.quote || '';
      targetAnnot.annotationComment =
        `【论证支撑证据卡 ${cardData.card_id}】\n要点：${cardData.summary || ''}\n置信度评分：${cardData.score || 8}/10`;
      targetAnnot.annotationColor = '#ffd400'; // Gold yellow
      targetAnnot.annotationPageLabel = pageLabel;
      targetAnnot.annotationSortIndex = sortIndex;
      targetAnnot.annotationPosition = JSON.stringify({
        pageIndex: pageIndex,
        rects: rects
      });
      targetAnnot.setTags([
        { tag: '#evidence-temp' },
        { tag: cardTag }
      ]);
      await targetAnnot.saveTx();
      log(`Created temporary evidence highlight annotation: ${targetAnnot.key}`);
    } catch (err) {
      log(`Could not create annotation: ${err}`);
    }
  }

  let location = {
    pageIndex: pageIndex
  };
  if (targetAnnot) {
    location.annotationID = targetAnnot.key;
  }

  log(`Opening file attachment ${attachment.key} (id: ${attachment.id}) at pageIndex ${pageIndex}, annot: ${location.annotationID || 'none'}`);
  await Zotero.Reader.open(attachment.id, location);

  let win = Zotero.getMainWindow();
  if (win && win.focus) {
    win.focus();
  }
}

async function cleanupTempAnnotations() {
  try {
    await waitForZotero();
    let sql = `
      SELECT itemID FROM itemTags 
      JOIN tags USING (tagID) 
      WHERE name = '#evidence-temp'
    `;
    let ids = await Zotero.DB.columnQueryAsync(sql);
    let count = ids ? ids.length : 0;
    if (count > 0) {
      await Zotero.Items.erase(ids);
      log(`Cleaned up ${count} temporary evidence annotations`);
      let win = Zotero.getMainWindow();
      if (win && win.alert) {
        win.alert(`✅ 已成功清理 ${count} 条临时论据高亮标注！文库已恢复整洁。`);
      }
    } else {
      let win = Zotero.getMainWindow();
      if (win && win.alert) {
        win.alert(`当前没有需要清理的临时论据高亮标注。`);
      }
    }
    return count;
  } catch (e) {
    log(`Error in cleanupTempAnnotations: ${e}`);
    return 0;
  }
}

async function createCardFromSelection(reader, selectedText, pageIndex) {
  try {
    let text = (selectedText || (reader.getSelectedText && reader.getSelectedText()) || '').trim();
    if (!text) {
      return;
    }
    let attachment = reader._item || reader.item;
    let parentItem = null;
    if (attachment && attachment.parentID) {
      parentItem = await Zotero.Items.getAsync(attachment.parentID);
    }
    let pIdx = pageIndex != null ? pageIndex : (reader._internalReader?.pageIndex || 0);
    let pageLabel = String(pIdx + 1);

    let creators = [];
    if (parentItem) {
      try {
        creators = parentItem.getCreators().map(c => `${c.firstName || ''} ${c.lastName || ''}`.trim());
      } catch (ex) {}
    }

    let payload = {
      zotero_key: parentItem ? parentItem.key : '',
      attachment_key: attachment ? attachment.key : '',
      quote: text,
      page_index: pIdx,
      page_label: pageLabel,
      title: parentItem ? parentItem.getField('title') : (attachment ? attachment.getField('title') : 'Zotero 选文批注'),
      authors: JSON.stringify(creators),
      year: parentItem ? String(parentItem.getField('year') || '') : '',
      score: 8.5,
      claim: '从 Zotero 阅读器划选创建'
    };

    log(`Posting reverse card creation to SearchTools: ${JSON.stringify(payload).slice(0, 120)}...`);
    let data = await httpPost(`${SEARCHTOOLS_HOST}/api/evidence-cards/create-from-zotero`, payload);
    if (data && data.ok) {
      let win = Zotero.getMainWindow();
      if (win && win.alert) {
        win.alert(`📌 论据已成功保存为证据卡！\n卡片编号: ${data.card.id}\n文献: ${data.card.title}\n第 ${pageLabel} 页\n已自动同步至证据工作台与开题报告引用库。`);
      }
    }
  } catch (e) {
    log(`Error in createCardFromSelection: ${e}`);
  }
}

function startActionPoller() {
  if (pollIntervalId) return;
  pollIntervalId = setInterval(async () => {
    try {
      let data = await httpGet(`${SEARCHTOOLS_HOST}/api/zotero/actions`);
      let actions = data.actions || [];
      for (let act of actions) {
        if (act.action === 'open_card' && act.card_id) {
          log(`Poller received open_card action for ${act.card_id}`);
          if (act.locate && act.locate.rects) {
            await highlightAndOpenCard(act.locate);
          } else {
            await handleCardOpen(act.card_id);
          }
        } else if (act.action === 'cleanup') {
          await cleanupTempAnnotations();
        }
      }
    } catch (e) {
      // Server might be paused or starting, ignore silent network error
    }
  }, 1000);
}

function stopActionPoller() {
  if (pollIntervalId) {
    clearInterval(pollIntervalId);
    pollIntervalId = null;
  }
}

// Zotero 7/8 Plugin Lifecycle Hooks
async function startup(data, reason) {
  let version = (data && data.version) || '1.0.0';
  dump(`[Evidence Bridge] Starting SearchTools Evidence Bridge v${version}...\n`);

  await waitForZotero();
  log(`Zotero initialized, registering bridge hooks...`);

  // 1. Register Custom Protocol: zotero://evidence/open?card=...
  try {
    let handler = Services.io.getProtocolHandler('zotero').wrappedJSObject;
    if (handler && handler._extensions) {
      handler._extensions['zotero://evidence'] = {
        noContent: true,
        doAction: async function (uri) {
          try {
            let spec = uri.spec || uri.pathQueryRef || '';
            let m = spec.match(/[?&]card=([^&]+)/);
            let cardId = m ? decodeURIComponent(m[1]) : '';
            if (!cardId) {
              let parts = spec.split('?')[0].split('/').filter(Boolean);
              cardId = parts[parts.length - 1];
            }
            if (cardId) {
              await handleCardOpen(cardId);
            }
          } catch (e) {
            log(`Error in protocol handler: ${e}`);
          }
        },
        newChannel: function (uri) {
          this.doAction(uri);
        }
      };
      log('Registered zotero://evidence protocol extension');
    }
  } catch (ex) {
    log(`Error registering protocol handler: ${ex}`);
  }

  // 2. Start Action Poller with SearchTools
  startActionPoller();

  // 3. Register Reader UI Listeners (Reader text selection popup + toolbar)
  if (Zotero.Reader && Zotero.Reader.registerEventListener) {
    try {
      // Selection Popup: Add "📌 存为证据卡"
      Zotero.Reader.registerEventListener(
        'renderTextSelectionPopup',
        (event) => {
          let { reader, doc, params, append } = event;
          let btn = doc.createElement('button');
          btn.className = 'evidence-bridge-popup-btn';
          btn.textContent = '📌 存为证据卡';
          btn.title = '将划选文字存入 SearchTools 证据卡库并在开题报告中直接引用';
          btn.style.cssText =
            'background:#1e293b;color:#facc15;border:1px solid #eab308;border-radius:4px;padding:3px 8px;font-size:12px;font-weight:bold;cursor:pointer;margin-left:4px;display:inline-flex;align-items:center;';
          btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            let text = (params && params.annotation && params.annotation.text) || (reader.getSelectedText && reader.getSelectedText());
            let pageIndex = (params && params.annotation && params.annotation.position && params.annotation.position.pageIndex != null) ? params.annotation.position.pageIndex : (reader._internalReader?.pageIndex || 0);
            await createCardFromSelection(reader, text, pageIndex);
          });
          append(btn);
        },
        ADDON_ID
      );

      // Reader Toolbar: Add "🧹 清除临时高亮"
      Zotero.Reader.registerEventListener(
        'renderToolbar',
        (event) => {
          let { doc, append } = event;
          let btn = doc.createElement('button');
          btn.className = 'evidence-bridge-toolbar-btn';
          btn.textContent = '🧹 清除证据临时高亮';
          btn.title = '一键清理所有临时生成的论据高亮标注，保持文库整洁';
          btn.style.cssText =
            'background:none;border:1px solid #94a3b8;color:#475569;border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer;margin:0 4px;';
          btn.addEventListener('click', async () => {
            await cleanupTempAnnotations();
          });
          append(btn);
        },
        ADDON_ID
      );
      log('Registered Reader event listeners successfully');
    } catch (e) {
      log(`Error registering Reader event listeners: ${e}`);
    }
  }

  // 4. Attach Tools Menu Item in Main Window
  try {
    let win = Zotero.getMainWindow();
    if (win) {
      onMainWindowLoad({ window: win });
    }
  } catch (e) {
    log(`Error on attaching menu item: ${e}`);
  }

  log('SearchTools Evidence Bridge started successfully!');
}

function onMainWindowLoad({ window }) {
  try {
    let doc = window.document;
    let menu = doc.getElementById('menu_ToolsPopup');
    if (menu && !doc.getElementById('evidence-bridge-tools-menu')) {
      let sep = doc.createElement('menuseparator');
      sep.id = 'evidence-bridge-tools-sep';
      menu.appendChild(sep);

      let item = doc.createElement('menuitem');
      item.id = 'evidence-bridge-tools-menu';
      item.setAttribute('label', '🧹 清除证据临时高亮 (SearchTools)');
      item.addEventListener('command', async () => {
        await cleanupTempAnnotations();
      });
      menu.appendChild(item);
      log('Attached menuitem to Tools popup');
    }
  } catch (e) {
    log(`Error in onMainWindowLoad: ${e}`);
  }
}

function onMainWindowUnload({ window }) {
  try {
    let doc = window.document;
    let item = doc.getElementById('evidence-bridge-tools-menu');
    if (item) item.remove();
    let sep = doc.getElementById('evidence-bridge-tools-sep');
    if (sep) sep.remove();
  } catch (e) {}
}

function shutdown() {
  log('Shutting down SearchTools Evidence Bridge...');
  stopActionPoller();
  let win = Zotero ? Zotero.getMainWindow() : null;
  if (win) {
    onMainWindowUnload({ window: win });
  }
}

function install() {}
function uninstall() {}
