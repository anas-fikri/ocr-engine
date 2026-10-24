<?php
/**
 * Client Photo Quality Engine untuk PHP 7.4+ (Laravel, CodeIgniter, PHP native). Butuh ekstensi curl & json.
 * Salin file ini ke project Anda (contoh Laravel: app/Services/PhotoQualityClient.php lalu sesuaikan namespace).
 *
 * PRINSIP: engine TIDAK BOLEH menghambat proses kurir. Semua method "fail-open":
 * jika engine lambat/mati, hasil ['ok' => false, 'unavailable' => true] dan TIDAK melempar exception.
 *
 * Engine HANYA memproses gambar. Sebelum dan sesudahnya (termasuk menyimpan ke SILO) dikerjakan aplikasi Anda.
 *
 * Cara utama (satu panggilan, satu keputusan):
 *   $pqe = new PhotoQualityClient('https://photo-quality.pcpexpress.com', getenv('PQE_API_KEY'));
 *   $key = 'pod/2026/10/24/AWB123.jpg';   // key SILO yang akan dipakai aplikasi
 *   $d = $pqe->checkFile($pathFoto, ['awb' => 'AWB123', 'photo_type' => 'pod_recipient'], 3000, $key);
 *   if ($d['message']) { ... kirim $d['message'] ke aplikasi kurir ... }
 *   if (!$d['allow_status_update']) { ... foto ditolak: jangan disimpan, minta foto ulang, status JANGAN diupdate ... }
 *   $isi = $d['image']['data'] ?? file_get_contents($pathFoto);   // image null = engine tidak menjawab -> foto asli
 *   ... simpan $isi ke SILO di $key, update status pengiriman ...
 */

class PhotoQualityClient
{
    private string $baseUrl;
    private string $apiKey;
    private int $timeoutMs;
    private ?string $lang;
    private ?string $tenant;

    public function __construct(string $baseUrl, string $apiKey, int $timeoutMs = 5000, ?string $lang = null, ?string $tenant = null)
    {
        $this->baseUrl = rtrim($baseUrl, '/');
        $this->apiKey = $apiKey;
        $this->timeoutMs = $timeoutMs;
        $this->lang = $lang;
        $this->tenant = $tenant;
    }

    /**
     * Alternatif jika foto sudah terlanjur disimpan ke SILO: kirim key-nya, tunggu keputusan maks. $timeoutMs. Tidak pernah throw.
     * Return: allow_status_update (false = JANGAN update status, minta foto ulang), message (tampilkan ke kurir jika
     * tidak null), retake_required, decided (false = engine mati/lambat/error -> fail-open), photo_id, verdict.
     */
    public function checkObject(string $objectKey, array $fields = [], int $timeoutMs = 3000, ?string $bucket = null): array
    {
        return self::toDecision($this->analyzeObject($objectKey, $fields, true, $timeoutMs, $bucket));
    }

    /**
     * CARA UTAMA. Kirim file ke engine; engine memeriksa dan mengoptimasi, TIDAK menyimpan apa pun.
     * Diterima -> 'image' = ['data' => isi file, 'content_type', 'optimized', 'sha256']: simpan ini ke SILO.
     * Ditolak -> allow_status_update false + message, 'image' null.
     * decided false (engine mati/lambat) -> simpan foto asli seperti sekarang, lalu lanjut.
     * $objectKey (opsional): key SILO yang akan dipakai aplikasi, supaya Web UI/evidence engine bisa menampilkan fotonya.
     */
    public function checkFile(string $path, array $fields = [], int $timeoutMs = 3000, ?string $objectKey = null, ?string $bucket = null): array
    {
        return self::toDecision($this->analyzeFile($path, $fields, $timeoutMs, $objectKey, $bucket));
    }

    /** Opsional: laporkan key SILO setelah foto disimpan (jika belum dikirim sebagai $objectKey di checkFile). */
    public function reportStored(string $photoId, string $objectKey, ?string $bucket = null): array
    {
        $body = array_merge(['object_key' => $objectKey], $bucket ? ['bucket' => $bucket] : []);
        return $this->request('POST', '/v1/photos/' . rawurlencode($photoId) . '/stored', [], json_encode($body),
            ['Content-Type: application/json']);
    }

    /** Ubah hasil API menjadi keputusan. Tanpa hasil (engine mati, timeout, error) -> fail-open. */
    public static function toDecision(array $r): array
    {
        $open = ['allow_status_update' => true, 'message' => null, 'retake_required' => false, 'decided' => false,
                 'photo_id' => null, 'verdict' => null, 'image' => null];
        if (!$r['ok']) {
            return $open + ['error' => $r['error']];
        }
        $p = $r['data'];
        if (($p['status'] ?? null) === 'pending') {
            return array_merge($open, ['photo_id' => $p['id'] ?? null]);
        }
        return [
            'allow_status_update' => $p['allow_status_update'] ?? (($p['action'] ?? null) !== 'reject'),
            'message' => $p['courier_message'] ?? null,
            'retake_required' => (bool) ($p['retake_required'] ?? false),
            'decided' => true,
            'photo_id' => $p['id'] ?? null,
            'verdict' => $p['verdict'] ?? null,
            'image' => self::decodeImage($p['output_image'] ?? null), // simpan ke SILO jika tidak null
        ];
    }

    private static function decodeImage(?array $o): ?array
    {
        if (!$o) {
            return null;
        }
        return ['data' => base64_decode($o['base64']), 'content_type' => $o['content_type'],
                'optimized' => (bool) $o['optimized'], 'sha256' => $o['sha256']];
    }

    /** Foto sudah disimpan aplikasi di SILO; engine hanya membacanya. Kirim key objeknya saja. $fields: awb, photo_type, courier_id, customer_id, branch, captured_at, lat, lng, source, external_id */
    public function analyzeObject(string $objectKey, array $fields = [], bool $sync = false, int $syncTimeoutMs = 3000, ?string $bucket = null): array
    {
        $body = array_merge(['object_key' => $objectKey], $bucket ? ['bucket' => $bucket] : [], $fields);
        $query = $sync ? ['sync' => 'true', 'timeout_ms' => $syncTimeoutMs] : [];
        return $this->request('POST', '/v1/analyze', $query, json_encode($body), ['Content-Type: application/json'], $syncTimeoutMs + 2000);
    }

    /** Kirim file ke engine untuk dianalisis (selalu langsung, maks. $syncTimeoutMs). Engine tidak menyimpan file-nya. */
    public function analyzeFile(string $path, array $fields = [], int $syncTimeoutMs = 3000, ?string $objectKey = null, ?string $bucket = null): array
    {
        $post = ['file' => new CURLFile($path, mime_content_type($path) ?: 'image/jpeg', basename($path))];
        foreach ($fields as $k => $v) {
            if ($v !== null) {
                $post[$k] = (string) $v;
            }
        }
        if ($objectKey) {
            $post['object_key'] = $objectKey;
        }
        if ($bucket) {
            $post['bucket'] = $bucket;
        }
        return $this->request('POST', '/v1/analyze', ['timeout_ms' => $syncTimeoutMs], $post, [], $syncTimeoutMs + 15000);
    }

    public function getPhoto(string $id): array
    {
        return $this->request('GET', '/v1/photos/' . rawurlencode($id));
    }

    public function findByAwb(string $awb): array
    {
        return $this->request('GET', '/v1/photos', ['awb' => $awb]);
    }

    /** Data bukti pengiriman (hanya foto layak) untuk pelanggan. */
    public function evidence(string $awb): array
    {
        return $this->request('GET', '/v1/evidence/' . rawurlencode($awb));
    }

    /** PDF bukti pengiriman. Return ['ok' => true, 'data' => <bytes PDF>] */
    public function evidencePdf(string $awb): array
    {
        return $this->request('GET', '/v1/evidence/' . rawurlencode($awb) . '/pdf', [], null, [], 30000, false);
    }

    /**
     * Verifikasi tanda tangan webhook. $rawBody = body mentah (Laravel: $request->getContent()).
     *   PhotoQualityClient::verifyWebhook($secret, $request->header('X-PQE-Timestamp'), $request->getContent(), $request->header('X-PQE-Signature'))
     */
    public static function verifyWebhook(string $secret, ?string $timestamp, string $rawBody, ?string $signature, int $toleranceSec = 300): bool
    {
        if (!$timestamp || !$signature || abs(time() - (int) $timestamp) > $toleranceSec) {
            return false;
        }
        $expected = 'sha256=' . hash_hmac('sha256', $timestamp . '.' . $rawBody, $secret);
        return hash_equals($expected, $signature);
    }

    private function request(string $method, string $path, array $query = [], $body = null, array $headers = [], ?int $timeoutMs = null, bool $json = true): array
    {
        if ($this->tenant) {
            $query['tenant'] = $this->tenant;
        }
        if ($this->lang) {
            $query['lang'] = $this->lang;
        }
        $url = $this->baseUrl . $path . ($query ? '?' . http_build_query($query) : '');
        $ch = curl_init($url);
        $headers[] = 'X-API-Key: ' . $this->apiKey;
        if ($this->lang) {
            $headers[] = 'Accept-Language: ' . $this->lang;
        }
        curl_setopt_array($ch, [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_CONNECTTIMEOUT_MS => 2000,
            CURLOPT_TIMEOUT_MS => $timeoutMs ?? $this->timeoutMs,
        ]);
        if ($body !== null) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
        }
        $raw = curl_exec($ch);
        $status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
        $err = curl_error($ch);
        curl_close($ch);

        if ($raw === false) { // timeout / jaringan putus -> fail-open
            return ['ok' => false, 'status' => 0, 'unavailable' => true, 'error' => $err];
        }
        $data = $json ? json_decode($raw, true) : $raw;
        if ($status >= 200 && $status < 300) {
            return ['ok' => true, 'status' => $status, 'data' => $data];
        }
        $msg = is_array($data) && isset($data['error']['message']) ? $data['error']['message'] : ('HTTP ' . $status);
        return ['ok' => false, 'status' => $status, 'unavailable' => $status >= 500, 'error' => $msg];
    }
}
