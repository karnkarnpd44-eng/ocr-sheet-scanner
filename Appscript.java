var LINE_ACCESS_TOKEN = "L7RIVNhGtYj0lNv3iA0ISVllSO8xiv7+OU0ZCB8Yfp+3wlqxfsET0m5HtOtadzKxZf4Fbfwbg2obz1IKElIEyEmeHYl4JHM4rM9PYSz4EnEt92vG5jExY6YMWRAMrap7iKNsOoU/TKz9i3XbynnV6QdB04t89/1O/w1cDnyilFU=";
var SPREADSHEET_ID = "1sAFf6gflCwokYG_zJVzqNeGug7cFQF6QPu26Qokh56U";

function doPost(e) {
  if (typeof e === "undefined" || !e.postData) return;
  try {
    var json = JSON.parse(e.postData.contents);
    if (!json.events || json.events.length === 0) return;
    replyFromSheet(json);
  } catch (err) {
    console.error("doPost Error: " + err);
  }
}

// ฟังก์ชันสร้าง Flex Message ที่ปลอดภัยจาก Error ค่าว่าง
function createFlexStatusMessage(code, status) {
  var rawStatus = String(status || "").trim();
  var displayStatus = "ข้อมูลยังไม่เข้าระบบ";
  var statusColor = "#94A3B8"; // สีเทาอ่อน
  var statusIcon = "⏳";

  // ตรวจสอบสถานะอย่างชัดเจน
  if (rawStatus === "" || rawStatus === "null" || rawStatus === "undefined") {
    displayStatus = "ข้อมูลยังไม่เข้าระบบ";
    statusColor = "#94A3B8"; // สีเทาอ่อน
    statusIcon = "⏳";
  } else if (rawStatus.indexOf("รับข้อมูลเข้าระบบแล้ว") !== -1) {
    displayStatus = rawStatus;
    statusColor = "#EA580C"; // สีส้ม
    statusIcon = "📥";
  } else if (rawStatus.indexOf("กำลังปัก") !== -1) {
    displayStatus = rawStatus;
    statusColor = "#EAB308"; // สีเหลือง
    statusIcon = "🧵";
  } else if (rawStatus.indexOf("รอเย็บชุดลูกเสือ") !== -1) {
    displayStatus = rawStatus;
    statusColor = "#0284C7"; // สีฟ้า
    statusIcon = "🪡";
  } else if (rawStatus.indexOf("รอปักดาว/จุด") !== -1) {
    displayStatus = rawStatus;
    statusColor = "#0284C7"; // สีฟ้า
    statusIcon = "⭐";
  } else if (rawStatus.indexOf("ปักเสร็จแล้ว") !== -1) {
    displayStatus = rawStatus;
    statusColor = "#16A34A"; // สีเขียว
    statusIcon = "✅";
  } else if (rawStatus.indexOf("ลูกค้ารับแล้ว") !== -1) {
    displayStatus = rawStatus;
    statusColor = "#EC4899"; // สีชมพู
    statusIcon = "🛍️️";
  } else {
    // สถานะพิมพ์เอง
    displayStatus = rawStatus;
    statusColor = "#DC2626"; // สีแดง
    statusIcon = "✏️";
  }

  // ป้องกันรหัสเป็นค่าว่าง
  var safeCode = String(code || "-").trim();
  if (safeCode === "") safeCode = "-";

  return {
    type: "flex",
    altText: "🧵 ว.เซ็นเตอร์ แจ้งเตือน: รหัส " + safeCode + " สถานะ " + displayStatus,
    contents: {
      type: "bubble",
      size: "giga",
      direction: "ltr",
      header: {
        type: "box",
        layout: "vertical",
        contents: [
          {
            type: "text",
            text: "🧵 ว.เซ็นเตอร์ 🧵",
            weight: "bold",
            size: "3xl",
            color: "#F7690A",
            align: "center",
            wrap: false
          },
          {
            type: "text",
            text: "ถูกครบ จบที่เดียว",
            weight: "bold",
            size: "xs",
            color: "#94A3B8",
            align: "center",
            wrap: false
          },
          {
            type: "separator",
            margin: "md"
          }
        ]
      },
      hero: {
        type: "image",
        url: "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcQZ72HAyev-Fr5x-3uwbKg2uKWDzsuFeyeTnxVEF3g3bvCWDZne2mYKKZA&s=10",
        size: "full",
        aspectRatio: "1.51:1",
        aspectMode: "fit"
      },
      body: {
        type: "box",
        layout: "vertical",
        spacing: "md",
        contents: [
          {
            type: "text",
            text: "📢 แจ้งเตือนสถานะงานปัก",
            weight: "bold",
            color: "#1E3A8A",
            align: "center",
            size: "lg"
          },
          {
            type: "box",
            layout: "vertical",
            margin: "md",
            spacing: "sm",
            backgroundColor: "#F8FAFC",
            cornerRadius: "md",
            paddingAll: "md",
            contents: [
              {
                type: "box",
                layout: "baseline",
                spacing: "sm",
                contents: [
                  {
                    type: "text",
                    text: "🏷️ รหัสงาน :",
                    color: "#64748B",
                    size: "md",
                    flex: 2
                  },
                  {
                    type: "text",
                    text: safeCode,
                    weight: "bold",
                    color: "#0F172A",
                    size: "lg",
                    flex: 3
                  }
                ]
              },
              {
                type: "box",
                layout: "baseline",
                spacing: "sm",
                contents: [
                  {
                    type: "text",
                    text: statusIcon + " สถานะ :",
                    color: "#64748B",
                    size: "md",
                    flex: 2
                  },
                  {
                    type: "text",
                    text: displayStatus,
                    weight: "bold",
                    color: statusColor,
                    size: "lg",
                    flex: 3
                  }
                ]
              }
            ]
          },
          {
            type: "text",
            text: "ขอบคุณที่ใช้บริการ ว.เซ็นเตอร์ ครับ/ค่ะ 🙏",
            color: "#94A3B8",
            size: "xs",
            align: "center",
            margin: "sm"
          }
        ]
      },
      footer: {
        type: "box",
        layout: "horizontal",
        contents: [
          {
            type: "button",
            style: "primary",
            color: "#F7690A",
            action: {
              type: "message",
              label: "🔄 เช็คสถานะอีกครั้ง",
              text: safeCode
            }
          }
        ]
      }
    }
  };
}

function replyFromSheet(data) {
  var replyUrl = "https://api.line.me/v2/bot/message/reply";
  var ss = SpreadsheetApp.openById(SPREADSHEET_ID);
  var sh = ss.getSheetByName("2026");

  var event = data.events[0];
  var reply_token = event.replyToken;
  var userText = (event.message.text || "").trim();

  var targetRow = -1;
  var foundCode = userText;
  var foundStatus = "";
  var isCodeFound = false;

  // ใช้สูตร MATCH ค้นหาตำแหน่งแถวทันทีแบบเดิม (ไม่ต้องวนลูปให้อืด)
  if (userText !== "") {
    try {
      // ค้นหาแบบตรงตัว หรือแบบตัวเลข
      var numVal = !isNaN(userText) ? Number(userText) : userText;
      
      // ดึงคอลัมน์ A ทั้งหมดมาหา index โดยตรง
      var codeList = sh.getRange("A3:A5002").getValues();
      for (var i = 0; i < codeList.length; i++) {
        var cellVal = codeList[i][0];
        if (cellVal === "" || cellVal === null || typeof cellVal === "undefined") continue;
        
        if (String(cellVal).trim() === userText || (!isNaN(cellVal) && Number(cellVal) === numVal)) {
          targetRow = 3 + i;
          isCodeFound = true;
          break;
        }
      }

      if (isCodeFound) {
        // ดึงสถานะจากคอลัมน์ B ทันที
        foundStatus = String(sh.getRange(targetRow, 2).getValue() || "").trim();
      }
    } catch (e) {
      console.error("Search Error: " + e);
    }
  }

  var messageArray = [];

  if (isCodeFound) {
    messageArray.push(createFlexStatusMessage(foundCode, foundStatus));
  } else {
    messageArray.push({
      type: "text",
      text: "ขออภัยค่ะ **ระบบอัตโนมัติไม่สามารถสนทนาได้** สอบถามเสื้อปัก พิมพ์รหัส บนใบรับเสื้อสีฟ้า เท่านั้น / ร้านเปิด 7.00 - 19.00 น. **กรณีทำบัตรรับเสื้อหาย สามารถแจ้งชื่อที่หน้าร้านได้ค่ะ **ขออภัยไม่รับแก้ชื่อในไลน์ รบกวนลูกค้าแจ้งหน้าร้านได้เลยค่ะ "
    });
  }

  var options = {
    method: "post",
    headers: {
      "Content-Type": "application/json; charset=UTF-8",
      "Authorization": "Bearer " + LINE_ACCESS_TOKEN
    },
    payload: JSON.stringify({
      replyToken: reply_token,
      messages: messageArray
    }),
    muteHttpExceptions: true
  };

  // ส่งตอบกลับทันที
  try {
    UrlFetchApp.fetch(replyUrl, options);
  } catch (err) {
    console.error("Fetch Reply Error: " + err);
  }

  // บันทึก User ID ลงคอลัมน์ H
  if (targetRow !== -1 && event.source && event.source.userId) {
    try {
      sh.getRange(targetRow, 8).setValue(event.source.userId);
    } catch (err) {
      console.error("Save User ID Error: " + err);
    }
  }
}

function handleSheetEdit(e) {
  if (!e || !e.range) return;
  var range = e.range;
  var sheet = range.getSheet();
  if (sheet.getName() !== "2026") return;

  var row = range.getRow();
  var col = range.getColumn();

  if (col === 2 && row >= 3) {
    var newStatus = String(range.getValue()).trim();
    if (newStatus === "") return;

    var timestamp = Utilities.formatDate(new Date(), "Asia/Bangkok", "yyyy-MM-dd HH:mm:ss");
    sheet.getRange(row, 7).setValue(timestamp);

    var cellBg = "#F87171";
    if (newStatus.indexOf("รับข้อมูลเข้าระบบแล้ว") !== -1) {
      cellBg = "#FDBA74";
    } else if (newStatus.indexOf("กำลังปัก") !== -1) {
      cellBg = "#FEF08A";
    } else if (newStatus.indexOf("รอเย็บชุดลูกเสือ") !== -1 || newStatus.indexOf("รอปักดาว/จุด") !== -1) {
      cellBg = "#BAE6FD";
    } else if (newStatus.indexOf("ปักเสร็จแล้ว") !== -1) {
      cellBg = "#BBF7D0";
    } else if (newStatus.indexOf("ลูกค้ารับแล้ว") !== -1) {
      cellBg = "#FBCFE8";
    }
    sheet.getRange(row, 1, 1, 2).setBackground(cellBg);

    var code = sheet.getRange(row, 1).getValue();
    var userId = sheet.getRange(row, 8).getValue();

    if (!userId || String(userId).trim() === "") return;

    sendPushFlex(String(userId).trim(), code, newStatus);
  }
}

function sendPushFlex(toUserId, code, status) {
  var pushUrl = "https://api.line.me/v2/bot/message/push";
  var flexMessage = createFlexStatusMessage(code, status);

  var payload = {
    to: toUserId,
    messages: [flexMessage]
  };

  var options = {
    method: "post",
    headers: {
      "Content-Type": "application/json; charset=UTF-8",
      "Authorization": "Bearer " + LINE_ACCESS_TOKEN
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  };

  try {
    UrlFetchApp.fetch(pushUrl, options);
  } catch (err) {
    console.error("ส่ง Push Message ผิดพลาด: " + err);
  }
}