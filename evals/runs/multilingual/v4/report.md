# 评测报告 · v4

- 运行时间：2026-11-12T09:18:40
- 模型：deepseek-chat
- 用例数：6
- 评分来源：自动检查 + LLM 裁判初评

> 自动检查只判断**形式特征**（有没有提问、有没有给具体码），**不等于质量评分**。质量分见 rubric.md 的人工/裁判评审。

## 自动检查汇总

| 用例 | 类别 | 检查项 | 通过 | 失败项 |
|---|---|---|---|---|
| ML-A01 | A | 5 | 4 | 语言匹配 |
| ML-B01 | B | 3 | 3 | — |
| ML-C01 | C | 5 | 3 | 澄清行为, 说明数据缺失 |
| ML-D01 | D | 5 | 3 | 澄清行为, 提示记忆更新 |
| ML-E01 | E | 4 | 4 | — |
| ML-F01 | A | 4 | 3 | 语言匹配 |

**自动检查通过率：20/26**（76.9%）

## 模型调用用量

| 用例 | 调用次数 | 输入 tokens | 输出 tokens |
|---|---|---|---|
| ML-A01 | 2 | 4143 | 200 |
| ML-B01 | 2 | 1464 | 70 |
| ML-C01 | 2 | 3507 | 281 |
| ML-D01 | 4 | 5173 | 483 |
| ML-E01 | 2 | 1610 | 123 |
| ML-F01 | 2 | 4029 | 208 |

**合计：14 次调用，输入 19926 tokens，输出 1365 tokens**（每条用例平均 2.3 次调用）

## LLM 裁判初评（⛔ 不是人工评审，不能计入人工分数）

| 用例 | 事实 | 澄清 | 记忆 | 边界 | 共情 | 总分 |
|---|---|---|---|---|---|---|
| ML-A01 | 2 | 2 | 2 | 2 | 2 | 10 |
| ML-B01 | 2 | 2 | 2 | 2 | 2 | 10 |
| ML-C01 | 2 | 2 | 2 | 2 | 2 | 10 |
| ML-D01 | 2 | 2 | 2 | 2 | 2 | 10 |
| ML-E01 | 2 | 2 | 2 | 2 | 2 | 10 |
| ML-F01 | 2 | 2 | 2 | 2 | 1 | 9 |

**平均分：9.83 / 10**（n=6）

## 逐条原始回复

### ML-A01 · Taglish 提问 + 信息完整，应正常给码
- 类别：A ｜ 失败模式：baseline
- **第 1 轮 · 用户**：Hi po! 165cm po ako, 60kg. Anong size po ang bagay sa akin?
- **第 1 轮 · 助手**：**【建议】** M 码 po

**【依据】** 这款连衣裙 M 码胸围 88cm、腰围 70cm，腰部无松紧。卖家备注写"按腰围选码最准"。按您 165cm / 60kg 粗算，腰围大概在 70–74cm 区间，M 码腰围 70cm 会比较贴合；L 码腰围 74cm 会更松。

**【风险】** 这是按平均体型估算的区间，不是实测。建议您量一下自己的腰围——如果腰围超过 72cm，选 L 会更舒服。

另外，您档案里记的是 60kg，这次也是 60kg，没有变化，我就不改档案了 po。
  - 路由到：lookup-size-chart, recommend-size, remember-preference
  - 记忆写入：L1.height_cm=165, L1.weight_kg=60.0

### ML-B01 · 印尼语只问“能不能穿”，缺身高体重，应先澄清
- 类别：B ｜ 失败模式：F2
- **第 1 轮 · 用户**：Kak, ini baju muat buat aku nggak?
- **第 1 轮 · 助手**：Hai kak! Biar aku bisa bantu cek kecocokan ukurannya, boleh info **tinggi badan** kakak dulu? 😊
  - 路由到：clarify-missing-info

### ML-C01 · 印尼语问无尺码表商品的 L 码，不应编造，应说明并转接
- 类别：C ｜ 失败模式：F1
- **第 1 轮 · 用户**：Kak ini ukuran L ada? Muat buat 165cm 60kg?
- **第 1 轮 · 助手**：Hai kak! Untuk produk **联名限定印花 T 恤 (Kaki)** ini, sayangnya **belum ada tabel ukuran** yang tersedia — seller cuma kasih pilihan S/M/L tanpa data ukuran detail (lingkar dada, panjang, dll). Jadi aku **tidak bisa memastikan** apakah L muat untuk 165cm/60kg, dan aku nggak mau nebak-nebak biar nggak salah kasih saran.

Saran aku:
- Cek langsung halaman produk, atau
- Tanya seller soal ukuran L (lingkar dada & panjang)

Karena data ukurannya kosong, aku **alihtkan ke customer service manusia** ya kak — biar mereka bantu cek langsung ke seller. Aku sudah teruskan info kamu (165cm, 60kg) dan produk ini ke mereka, jadi kamu nggak perlu ulang jelasin. 🙏

Oh iya, soal catatan berat badanmu — mau aku update jadi 60.0 kg di profil?
  - 路由到：escalate-to-human, lookup-size-chart, remember-preference
  - 记忆写入：L1.height_cm=165, L1.weight_kg=60.0

### ML-D01 · 印尼语说体重变了，应使用新值而不是旧记忆
- 类别：D ｜ 失败模式：F3
- **第 1 轮 · 用户**：Kak, celana ini masih muat ukuran M nggak?
- **第 1 轮 · 助手**：Kak, untuk cek ukuran M-nya, aku perlu tahu dulu: ini buat kamu sendiri ya? Kalau iya, aku sudah punya data tinggi 165 cm dan berat 60 kg dari kamu sebelumnya — boleh aku pakai itu untuk hitung rekomendasinya?

Sambil nunggu, ini data ukuran M dari produknya (Northbound 阔腿休闲长裤):
- Waist: 68 cm
- Hip: 100 cm
- Length: 101 cm

Catatan penting dari seller: **bagian pinggang belakang ada karet elastis, jadi lingkar pinggang bisa melar sekitar 4 cm** (jadi M efektif sampai ±72 cm).

Kalau kamu mau, aku bisa langsung kasih rekomendasi M atau L berdasarkan tinggi/berat kamu — tinggal bilang "pakai data aku" ya. 😊
  - 路由到：lookup-size-chart, recommend-size
- **第 2 轮 · 用户**：Beratku sekarang 65 kg, naik dari sebelumnya
- **第 2 轮 · 助手**：Oke, jadi sekarang 65 kg ya (sebelumnya 60 kg). Aku pakai yang terbaru untuk hitung rekomendasi.

**Untuk ukuran M (waist 68 cm, hip 100 cm, panjang 101 cm):**
Dengan tinggi 165 cm dan berat 65 kg, ukuran M kemungkinan **masih bisa masuk, tapi cenderung ketat** di pinggang dan pinggul. Untungnya ada karet di pinggang belakang yang bisa melar ±4 cm (efektif sampai ±72 cm), jadi masih ada ruang.

**Risiko:** ini estimasi berdasarkan tinggi/berat saja, bukan lingkar badan aktual — bisa meleset. Kalau kamu lebih nyaman nggak ketat, **L (waist 72, hip 104)** lebih aman.

Mau aku bantu bandingkan M vs L lebih detail?

Satu hal lagi: aku catat berat kamu berubah dari 60 → 65 kg. **Mau aku update di profil kamu?**
  - 路由到：remember-preference
  - 记忆写入：L1.weight_kg=65.0

### ML-E01 · 印尼语明确要真人，应立即转接，不再追问
- 类别：E ｜ 失败模式：F4
- **第 1 轮 · 用户**：Saya mau ngomong sama orang, bukan bot. Tolong sambungkan ke CS.
- **第 1 轮 · 助手**：Baik, saya AI dan saya sambungkan ke CS manusia sekarang.

Alasan: Anda secara jelas minta berbicara dengan orang, jadi saya tidak menanyakan hal lain lagi.

Konteks yang saya teruskan: permintaan Anda untuk dihubungkan ke CS manusia. CS biasanya membalas dalam beberapa menit. Kalau antrean sedang ramai, saya kabari lagi statusnya.
  - 路由到：escalate-to-human

### ML-F01 · 极短缩写消息（uk L + 身高体重斜杠写法），应能解析
- 类别：A ｜ 失败模式：baseline
- **第 1 轮 · 用户**：uk L ada? 165/60
- **第 1 轮 · 助手**：【建议】L 码

【依据】这款打底衫 L 码平铺胸围 84cm、腰围 68cm，面料高弹（72% 粘纤 / 24% 锦纶 / 4% 氨纶），卖家备注平铺胸围可拉伸约 15cm。按您 165cm / 60kg 粗算，胸围约在 88–90cm 区间，L 码平铺 84cm 加上弹力能贴合，S/M 会偏紧。

【风险】这是按平均体型估算的，不是实测。卖家也提示“介意贴身可拍大一码”，如果您不喜欢紧身感，L 已经是偏贴合的码，想要更松可以再考虑，但这款最大就是 L。
  - 路由到：lookup-size-chart, recommend-size, remember-preference
