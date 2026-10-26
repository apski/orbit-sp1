"""Env fixtures an adapter can declare: services tests need, owned by Voyager, never a shared server.

  [env.postgres]  its own cluster in ~/.voyager/<name>/pg on `port`: schema of `schemas` + data of `tables` copied
                  from `seed_from` (machine-local), optional self-signed ssl. Throwaway: `down --wipe` deletes it.
"""
import os
from pathlib import Path
import vy


def pg_docker_up(prof, c):
    """The same fixture as a container (hosts without Postgres binaries or sudo): 127.0.0.1 only, never exposed,
    SSL on with a throwaway self-signed cert, trust auth, seeded from `seed_file` (schema + reference data dump)."""
    name, port = f"voyager-{prof['name']}-pg", str(c['port'])
    if vy.sh(['docker', 'exec', name, 'pg_isready', '-U', 'voyager'])[0] == 0:
        return f'postgres (docker {name}) up on {port}'
    vy.sh(['docker', 'rm', '-f', name])
    certs = Path(prof['home']) / 'pg-certs'
    certs.mkdir(parents=True, exist_ok=True)
    if not (certs / 'server.crt').exists():
        vy.sh(['openssl', 'req', '-new', '-x509', '-days', '3650', '-nodes', '-subj', '/CN=localhost',
               '-keyout', str(certs / 'server.key'), '-out', str(certs / 'server.crt')], check=True)
    boot = ('cp /certs/server.crt /certs/server.key /tmp/ && chown postgres /tmp/server.* && chmod 600 /tmp/server.key '
            '&& exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key '
            f"-c fsync=off -c max_connections=200 -c timezone={c.get('timezone', 'UTC')}")
    vy.sh(['docker', 'run', '-d', '--name', name, '-p', f'127.0.0.1:{port}:5432', '-e', 'POSTGRES_USER=voyager',
           '-e', 'POSTGRES_DB=voyager', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', '-v', f'{certs}:/certs:ro',
           c['docker'], 'sh', '-c', boot], check=True)
    for _ in range(60):
        if vy.sh(['docker', 'exec', name, 'pg_isready', '-U', 'voyager'])[0] == 0:
            break
        import time; time.sleep(1)
    exts = ''.join(f'create extension if not exists "{e}";' for e in c.get('extensions', []))
    if exts:
        vy.sh(['docker', 'exec', name, 'psql', '-U', 'voyager', '-d', 'voyager', '-qc', exts], check=True)
    seed = Path(os.path.expanduser(c['seed_file']))
    cat = f"zstd -dc '{seed}'" if seed.suffix == '.zst' else f"cat '{seed}'"
    vy.sh(f"{cat} | docker exec -i {name} psql -U voyager -d voyager -q", timeout=1800)
    return f'postgres (docker {name}) up on {port} (created + seeded from {seed.name})'


def pg_up(prof):
    c = prof.get('env', {}).get('postgres')
    if not c:
        return 'no postgres fixture'
    if c.get('docker'):
        return pg_docker_up(prof, c)
    b = Path(c.get('bin', ''))
    pg = lambda x: str(b / x) if c.get('bin') else x
    d = Path(prof['home']) / 'pg'
    port = str(c['port'])
    q = ['-h', '127.0.0.1', '-p', port, '-U', 'voyager']
    if vy.sh([pg('pg_isready'), *q])[0] == 0:
        return f'postgres up on {port}'
    fresh = not (d / 'PG_VERSION').exists()
    if fresh:
        vy.sh([pg('initdb'), '-D', str(d), '-U', 'voyager', '--auth=trust', '-E', 'UTF8'], check=True)
        conf = [f"port = {port}", "listen_addresses = '127.0.0.1'", f"unix_socket_directories = '{d}'",
                f"timezone = '{c.get('timezone', 'UTC')}'", 'fsync = off', 'max_connections = 200']
        if c.get('ssl'):
            vy.sh(['openssl', 'req', '-new', '-x509', '-days', '3650', '-nodes', '-subj', '/CN=localhost',
                   '-keyout', str(d / 'server.key'), '-out', str(d / 'server.crt')], check=True)
            (d / 'server.key').chmod(0o600)
            conf.append('ssl = on')
        with open(d / 'postgresql.conf', 'a') as f:
            f.write('\n'.join(conf) + '\n')
    vy.sh([pg('pg_ctl'), '-D', str(d), '-l', str(d / 'log.txt'), '-w', 'start'], check=True)
    if fresh:
        vy.sh([pg('createdb'), *q, 'voyager'], check=True)
        exts = ''.join(f'create extension if not exists "{e}";' for e in c.get('extensions', []))
        if exts:
            vy.sh([pg('psql'), *q, '-d', 'voyager', '-qc', exts], check=True)
        src = c['seed_from']
        ns = [a for s in c.get('schemas', []) for a in ('-n', s)]
        tb = [a for t in c.get('tables', []) for a in ('-t', t)]
        load = f"{pg('psql')} {' '.join(q)} -d voyager -q"
        vy.sh(f"{pg('pg_dump')} '{src}' -s {' '.join(ns)} --no-owner --no-privileges | {load}", timeout=1800)
        if tb:
            vy.sh(f"{pg('pg_dump')} '{src}' -a --no-owner {' '.join(tb)} | {load}", timeout=1800)
    return f'postgres up on {port}' + (' (created + seeded)' if fresh else '')


def pg_down(prof, wipe=False):
    c = prof.get('env', {}).get('postgres') or {}
    if c.get('docker'):
        vy.sh(['docker', 'rm', '-f', f"voyager-{prof['name']}-pg"])
        return 'postgres (docker) removed'

    pg = lambda x: str(Path(c['bin']) / x) if c.get('bin') else x
    d = Path(prof['home']) / 'pg'
    vy.sh([pg('pg_ctl'), '-D', str(d), '-m', 'fast', 'stop'])
    if wipe:
        import shutil
        shutil.rmtree(d, ignore_errors=True)
    return 'postgres down' + (' + wiped' if wipe else '')


def up(prof):
    return [pg_up(prof)]


def down(prof, wipe=False):
    return [pg_down(prof, wipe)]
