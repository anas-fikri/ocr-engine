<?php
/**
 * Client Photo Quality Engine untuk PHP 7.4+ (Laravel, CodeIgniter, PHP native). Butuh ekstensi curl & json.
 * Salin file ini ke project Anda (contoh Laravel: app/Services/PhotoQualityClient.php lalu sesuaikan namespace).
 *
 * PRINSIP: engine TIDAK BOLEH menghambat proses kurir. Semua method "fail-open":
 * jika engine lambat/mati, hasil ['ok' => false, 'unavailable' => true] dan TIDAK melempar exception.
 *
 * Cara tercepat (satu panggilan, satu keputusan):
 *   $pqe = new PhotoQualityClient('https://photo-quality.pcpexpress.com', getenv('PQE_API_KEY'));
 *   $d = $pqe->checkObject('pod/2026/10/22/AWB123.jpg', ['awb' => 'AWB123', 'photo_type' => 'pod_recipient']);
 *   if ($d['message']) { ... kirim $d['message'] ke aplikasi kurir ... }
 *   if ($d['allow_status_update']) { ... update status pengiriman ... } // false = foto ditolak: minta foto ulang
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
     * CARA TERCEPAT. Foto sudah di SILO: kirim key-nya, tunggu keputusan maks. $timeoutMs. Tidak pernah throw.
     * Return: allow_status_update (false = JANGAN update status, minta foto ulang), message (tampilkan ke kurir jika
     * tidak null), retake_required, decided (false = engine mati/lambat/error -> fail-open), photo_id, verdict.
     */
    public function checkObject(string $objectKey, array $fields = [], int $timeoutMs = 3000, ?string $bucket = null): array
    {
        return self::toDecision($this->analyzeObject($objectKey, $fields, true, $timeoutMs, $bucket));
    }

    /** Sama dengan checkObject, tetapi file dikirim langsung (engine yang menyimpan ke SILO). */
    public function checkFile(string $path, array $fields = [], int $timeoutMs = 3000): array
    {
        return self::toDecision($this->analyzeFile($path, $fields, true, $timeoutMs));
    }

    /** Ubah hasil API menjadi keputusan. Tanpa hasil (engine mati, timeout, error) -> fail-open. */
    public static function toDecision(array $r): array
    {
        $open = ['allow_status_update' => true, 'message' => null, 'retake_required' => false, 'decided' => false,
                 'photo_id' => null, 'verdict' => null];
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
        ];
    }

    /** Foto sudah ada di SILO; kirim key objeknya saja. $fields: awb, photo_type, courier_id, customer_id, branch, captured_at, lat, lng, source, external_id */
    public function analyzeObject(string $objectKey, array $fields = [], bool $sync = false, int $syncTimeoutMs = 3000, ?string $bucket = null): array
    {
        $body = array_merge(['object_key' => $objectKey], $bucket ? ['bucket' => $bucket] : [], $fields);
        $query = $sync ? ['sync' => 'true', 'timeout_ms' => $syncTimeoutMs] : [];
        return $this->request('POST', '/v1/analyze', $query, json_encode($body), ['Content-Type: application/json'], $syncTimeoutMs + 2000);
    }

    /** Kirim file langsung ke engine (engine yang menyimpan ke SILO). */
    public function analyzeFile(string $path, array $fields = [], bool $sync = false, int $syncTimeoutMs = 3000): array
    {
        $post = ['file' => new CURLFile($path, mime_content_type($path) ?: 'image/jpeg', basename($path))];
        foreach ($fields as $k => $v) {
            if ($v !== null) {
                $post[$k] = (string) $v;
            }
        }
        $query = $sync ? ['sync' => 'true', 'timeout_ms' => $syncTimeoutMs] : [];
        return $this->request('POST', '/v1/analyze', $query, $post, [], $syncTimeoutMs + 15000);
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
