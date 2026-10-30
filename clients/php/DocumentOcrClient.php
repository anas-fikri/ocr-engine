<?php
/**
 * Client modul OCR dokumen (invoice dan jenis dokumen lain) untuk PHP 8.2+. Butuh ekstensi curl & json, tanpa
 * dependency lain. Salin ke project Anda (contoh Laravel: app/Services/DocumentOcrClient.php).
 *
 * Alur: submit() -> job_id, lalu getJob()/waitFor() sampai status succeeded/failed, lalu (opsional) sendCorrections()
 * setelah pengguna mengoreksi field agar engine belajar template per penerbit. Semua request ditandatangani HMAC.
 * Panggil HANYA dari server (api_secret tidak boleh ada di browser/HP).
 *
 *   $ocr = new DocumentOcrClient(getenv('PQE_URL'), getenv('PQE_API_KEY'), getenv('PQE_API_SECRET'));
 *   $job = $ocr->submit('/path/invoice.pdf', 'invoice', $npwpVendor);        // ['ok' => true, 'body' => ['job_id' => ...]]
 *   $res = $ocr->waitFor($job['body']['job_id'], 600);                        // atau getJob() dari queue worker Anda
 *   if ($res['ok'] && $res['body']['status'] === 'succeeded') { $fields = $res['body']['fields']; }
 *
 * Referensi: docs/api-reference.md#dokumen, docs/integration-guide.md#13-integrasi-modul-dokumen
 */

class DocumentOcrClient
{
    /** Jeda polling (detik): 3, 5, 10, 15, 30, lalu tiap 60. */
    public const POLL_SCHEDULE = [3, 5, 10, 15, 30];

    private string $baseUrl;
    private string $apiKey;
    private string $apiSecret;
    private int $timeoutPostMs;
    private int $timeoutGetMs;
    /** @var callable */
    private $sleep;

    public function __construct(string $baseUrl, string $apiKey, string $apiSecret, int $timeoutPostMs = 60000,
                                int $timeoutGetMs = 10000, ?callable $sleep = null)
    {
        $this->baseUrl = rtrim($baseUrl, '/');
        $this->apiKey = $apiKey;
        $this->apiSecret = $apiSecret;
        $this->timeoutPostMs = $timeoutPostMs;
        $this->timeoutGetMs = $timeoutGetMs;
        $this->sleep = $sleep ?? static function (int $s): void { sleep($s); };
    }

    /** X-Signature = hex(HMAC-SHA256(secret, ts \n METHOD \n path \n hex(sha256(body)))). path tanpa query. */
    public static function sign(string $secret, string $timestamp, string $method, string $path, string $body): string
    {
        $msg = $timestamp . "\n" . strtoupper($method) . "\n" . $path . "\n" . hash('sha256', $body);
        return hash_hmac('sha256', $msg, $secret);
    }

    /** Verifikasi callback dari engine (header X-Timestamp, X-Signature; path = path URL callback Anda). */
    public static function verifyCallback(string $secret, string $timestamp, string $signature, string $path,
                                          string $body, int $toleranceSec = 300): bool
    {
        if (!ctype_digit($timestamp) || abs(time() - (int) $timestamp) > $toleranceSec) {
            return false;
        }
        return hash_equals(self::sign($secret, $timestamp, 'POST', $path, $body), strtolower($signature));
    }

    /**
     * Kirim dokumen (path file atau isi file). $templateHint = ID penerbit jika diketahui (invoice: NPWP vendor).
     * Return ['ok', 'status', 'body'] (body sukses: job_id, status queued, poll_url; gagal: error.code).
     */
    public function submit(string $fileOrContent, string $documentType, ?string $templateHint = null,
                           ?string $callbackUrl = null, string $filename = 'document'): array
    {
        $content = is_file($fileOrContent) ? file_get_contents($fileOrContent) : $fileOrContent;
        if (is_file($fileOrContent)) {
            $filename = basename($fileOrContent);
        }
        $fields = ['document_type' => $documentType];
        if ($templateHint !== null && $templateHint !== '') {
            $fields['template_hint'] = $templateHint;
        }
        if ($callbackUrl !== null) {
            $fields['callback_url'] = $callbackUrl;
        }
        [$body, $contentType] = self::multipart($fields, $filename, $content);
        return $this->request('POST', '/v1/jobs', $body, $contentType, $this->timeoutPostMs);
    }

    public function getJob(string $jobId): array
    {
        return $this->request('GET', '/v1/jobs/' . rawurlencode($jobId), '', null, $this->timeoutGetMs);
    }

    /** Polling sampai succeeded/failed atau $maxSeconds habis (status terakhir dikembalikan). */
    public function waitFor(string $jobId, int $maxSeconds = 600): array
    {
        $waited = 0;
        $i = 0;
        while (true) {
            $delay = self::POLL_SCHEDULE[$i++] ?? 60;
            if ($waited + $delay > $maxSeconds) {
                return $this->getJob($jobId);
            }
            ($this->sleep)($delay);
            $waited += $delay;
            $r = $this->getJob($jobId);
            if ($r['ok'] && in_array($r['body']['status'] ?? '', ['succeeded', 'failed'], true)) {
                return $r;
            }
        }
    }

    /**
     * Kirim koreksi pengguna. $corrections = [['field' => 'total', 'extracted' => '...', 'corrected' => '...'], ...].
     * idempotency_key per koreksi diisi otomatis bila kosong; header Idempotency-Key dihitung dari key-key itu, jadi
     * mengirim ulang request yang sama aman.
     */
    public function sendCorrections(string $documentType, string $issuerId, string $jobId, array $corrections): array
    {
        $keys = [];
        foreach ($corrections as &$c) {
            $c['idempotency_key'] ??= self::correctionKey($jobId, $c['field'], $c['extracted'] ?? null, $c['corrected'] ?? null);
            $keys[] = $c['idempotency_key'];
        }
        unset($c);
        sort($keys, SORT_STRING);
        $body = json_encode(['document_type' => $documentType, 'issuer' => ['id' => $issuerId], 'job_id' => $jobId,
            'corrections' => $corrections], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        return $this->request('POST', '/v1/templates', $body, 'application/json', $this->timeoutGetMs,
            ['Idempotency-Key: ' . hash('sha256', implode("\n", $keys))]);
    }

    /** Kunci idempotensi yang disarankan: sha256(job_id \n field \n JSON(extracted) \n JSON(corrected)). */
    public static function correctionKey(string $jobId, string $field, $extracted, $corrected): string
    {
        $j = static fn($v) => json_encode($v, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        return hash('sha256', $jobId . "\n" . $field . "\n" . $j($extracted) . "\n" . $j($corrected));
    }

    /** @return array{0: string, 1: string} body multipart + Content-Type (body inilah yang ditandatangani). */
    public static function multipart(array $fields, string $filename, string $content): array
    {
        $boundary = 'pqe' . bin2hex(random_bytes(12));
        $body = '';
        foreach ($fields as $name => $value) {
            $body .= "--$boundary\r\nContent-Disposition: form-data; name=\"$name\"\r\n\r\n$value\r\n";
        }
        $safe = str_replace(['"', "\r", "\n"], '_', $filename);
        $body .= "--$boundary\r\nContent-Disposition: form-data; name=\"file\"; filename=\"$safe\"\r\n"
            . "Content-Type: application/octet-stream\r\n\r\n" . $content . "\r\n--$boundary--\r\n";
        return [$body, "multipart/form-data; boundary=$boundary"];
    }

    private function request(string $method, string $path, string $body, ?string $contentType, int $timeoutMs,
                             array $extraHeaders = []): array
    {
        $ts = (string) time();
        $headers = array_merge([
            'X-Api-Key: ' . $this->apiKey,
            'X-Timestamp: ' . $ts,
            'X-Signature: ' . self::sign($this->apiSecret, $ts, $method, $path, $body),
            'Accept: application/json',
            'Expect:',
        ], $extraHeaders);
        if ($contentType !== null) {
            $headers[] = 'Content-Type: ' . $contentType;
        }
        $ch = curl_init($this->baseUrl . $path);
        curl_setopt_array($ch, [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_TIMEOUT_MS => $timeoutMs,
            CURLOPT_CONNECTTIMEOUT_MS => min(5000, $timeoutMs),
            CURLOPT_FOLLOWLOCATION => false,
        ]);
        if ($method !== 'GET') {
            curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
        }
        $raw = curl_exec($ch);
        $status = (int) curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
        $err = curl_error($ch);
        curl_close($ch);
        if ($raw === false) {
            return ['ok' => false, 'status' => 0, 'body' => ['error' => ['code' => 'UNAVAILABLE', 'message' => $err]]];
        }
        $decoded = json_decode((string) $raw, true);
        return ['ok' => $status >= 200 && $status < 300, 'status' => $status, 'body' => is_array($decoded) ? $decoded : []];
    }
}
