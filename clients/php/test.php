<?php
/**
 * Test client PHP. Jalankan dengan PHP yang sudah di-patch (lihat README.md di folder ini):
 *   php clients/php/test.php
 * Uji online (opsional): set PQE_URL, PQE_API_KEY (role app), PQE_SAMPLE (path foto).
 */
require __DIR__ . '/PhotoQualityClient.php';
require __DIR__ . '/DocumentOcrClient.php';

// Versi minimal yang memuat security release 21 Okt 2026 (CVE-2026-109618, CVE-2026-109619).
const MIN_PATCHED = ['8.2' => '8.2.35', '8.3' => '8.3.36', '8.4' => '8.4.27', '8.5' => '8.5.12'];

$failed = 0;
function check(string $name, bool $cond, string $extra = ''): void
{
    global $failed;
    echo ($cond ? 'ok   ' : 'FAIL ') . $name . ($extra !== '' ? ' - ' . $extra : '') . "\n";
    if (!$cond) {
        $failed++;
    }
}

$branch = PHP_MAJOR_VERSION . '.' . PHP_MINOR_VERSION;
$min = MIN_PATCHED[$branch] ?? null;
check('PHP ' . PHP_VERSION . ' sudah di-patch', $min !== null && version_compare(PHP_VERSION, $min, '>='),
    $min ? "minimal $min" : 'gunakan PHP 8.2-8.5 terbaru');

$ts = (string) time();
$body = json_encode(['event' => 'analysis.completed', 'photo' => ['id' => 'x']]);
$sig = 'sha256=' . hash_hmac('sha256', $ts . '.' . $body, 'rahasia');
check('webhook valid diterima', PhotoQualityClient::verifyWebhook('rahasia', $ts, $body, $sig));
check('webhook diubah ditolak', !PhotoQualityClient::verifyWebhook('rahasia', $ts, $body . ' ', $sig));
check('webhook secret salah ditolak', !PhotoQualityClient::verifyWebhook('salah', $ts, $body, $sig));
check('webhook kedaluwarsa ditolak', !PhotoQualityClient::verifyWebhook('rahasia', (string) ($ts - 3600), $body, $sig));

$down = new PhotoQualityClient('http://127.0.0.1:9', 'x', 800);
$r = $down->analyzeObject('a.jpg', ['awb' => 'X'], true, 500);
check('engine mati -> fail-open tanpa exception', $r['ok'] === false && $r['unavailable'] === true);
$dDown = $down->checkObject('a.jpg', ['awb' => 'X'], 500);
check('checkObject engine mati -> status boleh diupdate', $dDown['allow_status_update'] === true && $dDown['decided'] === false && $dDown['message'] === null);

$photo = function (array $extra): array {
    return ['ok' => true, 'status' => 200, 'data' => array_merge(['id' => 'p1', 'status' => 'done', 'verdict' => 'REJECT', 'action' => 'reject'], $extra)];
};
$dReject = PhotoQualityClient::toDecision($photo(['allow_status_update' => false, 'courier_message' => 'Wajib foto ulang. Foto buram.', 'retake_required' => true]));
check('REJECT enforce -> status ditahan + pesan', $dReject['allow_status_update'] === false && $dReject['decided'] && $dReject['message'] === 'Wajib foto ulang. Foto buram.' && $dReject['retake_required']);
$dPass = PhotoQualityClient::toDecision($photo(['verdict' => 'PASS', 'action' => 'log', 'allow_status_update' => true, 'courier_message' => null, 'output_image' => ['content_type' => 'image/jpeg', 'bytes' => 3, 'optimized' => true, 'sha256' => 'x', 'base64' => base64_encode("\xff\xd8\xff")]]));
check('PASS -> status boleh, tanpa pesan, ada foto untuk disimpan', $dPass['allow_status_update'] === true && $dPass['message'] === null && $dPass['image']['data'] === "\xff\xd8\xff" && $dPass['image']['content_type'] === 'image/jpeg');
check('REJECT -> tanpa foto', $dReject['image'] === null);
check('engine mati -> tanpa foto (simpan foto asli)', $dDown['image'] === null);
$dPending = PhotoQualityClient::toDecision(['ok' => true, 'status' => 202, 'data' => ['id' => 'p2', 'status' => 'pending']]);
check('lewat 3 detik (pending) -> fail-open', $dPending['allow_status_update'] === true && $dPending['decided'] === false && $dPending['photo_id'] === 'p2');

$url = getenv('PQE_URL');
$key = getenv('PQE_API_KEY');
$sample = getenv('PQE_SAMPLE');
if ($url && $key && $sample) {
    $pqe = new PhotoQualityClient($url, $key, 5000, 'id');
    $res = $pqe->analyzeFile($sample, ['awb' => 'PHP-TEST', 'source' => 'camera']);
    check('analyzeFile', $res['ok'] && !empty($res['data']['verdict']),
        $res['ok'] ? $res['data']['verdict'] . ' ' . ($res['data']['reasons'][0]['message'] ?? '') : $res['error']);
    $d = $pqe->checkFile($sample, ['awb' => 'PHP-TEST-2', 'source' => 'camera']);
    check('checkFile', $d['decided'] === true && is_bool($d['allow_status_update']), $d['verdict']);
    if ($d['allow_status_update'] && $d['photo_id']) {
        check('foto untuk disimpan', $d['image'] !== null && strlen($d['image']['data']) > 0);
        $rep = $pqe->reportStored($d['photo_id'], 'php-test/PHP-TEST-2.jpg');
        check('reportStored', $rep['ok'] && $rep['data']['object']['key'] === 'php-test/PHP-TEST-2.jpg');
    }
    if ($res['ok']) {
        $got = $pqe->getPhoto($res['data']['id']);
        check('getPhoto', $got['ok'] && $got['data']['id'] === $res['data']['id']);
        $pdf = $pqe->evidencePdf('PHP-TEST');
        check('evidencePdf', $pdf['ok'] === false || strncmp($pdf['data'], '%PDF', 4) === 0, 'role app boleh akses evidence tenant sendiri');
    }
} else {
    echo "skip uji online (set PQE_URL, PQE_API_KEY, PQE_SAMPLE)\n";
}

// ---- client modul dokumen (offline): vektor tanda tangan sama dengan engine (engine/documents/signing.py)
check('dokumen: tanda tangan GET sesuai engine', DocumentOcrClient::sign('abc123', '1793337600', 'GET',
    '/v1/jobs/3f2a9c0e5b7d4e1f8a6b2c3d4e5f6a7b', '') === '452df4a4485a72a34b4335fde50ab551ed1937de296a0fb362f5f87285686595');
check('dokumen: tanda tangan POST sesuai engine', DocumentOcrClient::sign('abc123', '1793337600', 'post',
    '/v1/templates', '{"a":1}') === '84198d0b3fe8cd7d145b028d462c4a1096ce3c8a849996a6f8d54684e38fdd9c');
$cbTs = (string) time();
$cbBody = '{"event":"job.finished","job_id":"x","status":"succeeded"}';
$cbSig = DocumentOcrClient::sign('rahasia', $cbTs, 'POST', '/ocr/callback', $cbBody);
check('dokumen: callback valid diterima', DocumentOcrClient::verifyCallback('rahasia', $cbTs, $cbSig, '/ocr/callback', $cbBody));
check('dokumen: callback diubah ditolak', !DocumentOcrClient::verifyCallback('rahasia', $cbTs, $cbSig, '/ocr/callback', $cbBody . ' '));
check('dokumen: callback kedaluwarsa ditolak', !DocumentOcrClient::verifyCallback('rahasia', (string) (time() - 3600),
    DocumentOcrClient::sign('rahasia', (string) (time() - 3600), 'POST', '/ocr/callback', $cbBody), '/ocr/callback', $cbBody));
[$mp, $ct] = DocumentOcrClient::multipart(['document_type' => 'invoice'], 'a"b.pdf', "%PDF-1.4\nx");
check('dokumen: multipart memuat file & field', strpos($mp, "name=\"document_type\"\r\n\r\ninvoice") !== false
    && strpos($mp, 'filename="a_b.pdf"') !== false && strpos($ct, 'boundary=') !== false);
check('dokumen: correctionKey stabil', DocumentOcrClient::correctionKey('j', 'total', null, '100')
    === hash('sha256', "j\ntotal\nnull\n\"100\""));
$offline = new DocumentOcrClient('http://127.0.0.1:9', 'k', 's', 800, 800);
check('dokumen: engine mati -> error UNAVAILABLE tanpa exception', $offline->getJob('x')['body']['error']['code'] === 'UNAVAILABLE');

// ---- client modul dokumen (online, opsional): PQE_URL, PQE_DOC_API_KEY, PQE_DOC_API_SECRET, PQE_DOC_SAMPLE (PDF/JPEG/PNG)
if (getenv('PQE_URL') && getenv('PQE_DOC_API_KEY') && getenv('PQE_DOC_API_SECRET') && getenv('PQE_DOC_SAMPLE')) {
    $ocr = new DocumentOcrClient(getenv('PQE_URL'), getenv('PQE_DOC_API_KEY'), getenv('PQE_DOC_API_SECRET'), 60000, 10000,
        static function (int $s): void { sleep(min($s, 2)); });
    $job = $ocr->submit(getenv('PQE_DOC_SAMPLE'), 'invoice');
    check('dokumen: submit', $job['ok'] && isset($job['body']['job_id']), json_encode($job['body']));
    if ($job['ok']) {
        $res = $ocr->waitFor($job['body']['job_id'], 120);
        check('dokumen: job selesai', in_array($res['body']['status'] ?? '', ['succeeded', 'failed'], true), $res['body']['status'] ?? '');
        $bad = (new DocumentOcrClient(getenv('PQE_URL'), getenv('PQE_DOC_API_KEY'), 'secret-salah'))->getJob($job['body']['job_id']);
        check('dokumen: secret salah ditolak', $bad['status'] === 401 && $bad['body']['error']['code'] === 'INVALID_SIGNATURE');
    }
} else {
    echo "skip uji online dokumen (set PQE_URL, PQE_DOC_API_KEY, PQE_DOC_API_SECRET, PQE_DOC_SAMPLE)\n";
}

if ($failed) {
    fwrite(STDERR, "$failed test gagal\n");
    exit(1);
}
echo "semua test lolos\n";
