<?php
/**
 * Test client PHP. Jalankan dengan PHP yang sudah di-patch (lihat README.md di folder ini):
 *   php clients/php/test.php
 * Uji online (opsional): set PQE_URL, PQE_API_KEY (role app), PQE_SAMPLE (path foto).
 */
require __DIR__ . '/PhotoQualityClient.php';

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

$url = getenv('PQE_URL');
$key = getenv('PQE_API_KEY');
$sample = getenv('PQE_SAMPLE');
if ($url && $key && $sample) {
    $pqe = new PhotoQualityClient($url, $key, 5000, 'id');
    $res = $pqe->analyzeFile($sample, ['awb' => 'PHP-TEST', 'source' => 'camera'], true);
    check('analyzeFile sync', $res['ok'] && !empty($res['data']['verdict']),
        $res['ok'] ? $res['data']['verdict'] . ' ' . ($res['data']['reasons'][0]['message'] ?? '') : $res['error']);
    if ($res['ok']) {
        $got = $pqe->getPhoto($res['data']['id']);
        check('getPhoto', $got['ok'] && $got['data']['id'] === $res['data']['id']);
        $pdf = $pqe->evidencePdf('PHP-TEST');
        check('evidencePdf', $pdf['ok'] === false || strncmp($pdf['data'], '%PDF', 4) === 0, 'role app boleh akses evidence tenant sendiri');
    }
} else {
    echo "skip uji online (set PQE_URL, PQE_API_KEY, PQE_SAMPLE)\n";
}

if ($failed) {
    fwrite(STDERR, "$failed test gagal\n");
    exit(1);
}
echo "semua test lolos\n";
