import json
import os
import requests
from datetime import datetime
from loguru import logger
from urllib.parse import urljoin
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3.util.retry import Retry
from apscheduler.schedulers.blocking import BlockingScheduler


from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(current_dir, '.env'))

import notify

watch_interval = int(os.getenv('WATCH_INTERVAL', 10))


def requests_retry_session(
        retries=3,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 504),
        session=None,
):
    session = session or requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class BinanceLaunchPool:
    def __init__(self):
        self.headers = {
            "accept": "application/json",
            "accept-language": "zh-CN,zh;q=0.9",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                " AppleWebKit/537.36 (KHTML, like Gecko)"
                " Chrome/129.0.0.0 Safari/537.36"
            ),
            "lang": "zh-CN",
        }
        self.announce_url = (
            "https://www.binance.com/bapi/apex/v1/public/apex/cms/"
            "article/list/query?type=1&pageSize=20&pageNo=1"
        )

    def get_article_link(self, title, code):
        remove_chars = "()（）"
        translation_table = str.maketrans(" ，、", "---", remove_chars)
        sanitized_title = title.translate(translation_table)
        link = f"https://www.binance.com/zh-CN/support/announcement/{sanitized_title}-{code}"
        return link

    def timestamp_to_time(self, timestamp):
        seconds_timestamp = timestamp / 1000
        return datetime.fromtimestamp(seconds_timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def process_launchpool(self, article):
        """Process Launchpool events."""
        title = '币安 Launchpool'
        notify.send(title, article["title"])

    def process_superearn(self, article):
        """Process Super Earn events."""
        title = '币安超级赚币'
        notify.send(title, article["title"])

    def get_and_process_announces(self, cached_articles):
        ret = {}
        try:
            session = requests_retry_session()
            response = session.get(self.announce_url, headers=self.headers, timeout=5)
            response.raise_for_status()
            data = response.json()
            catalogs = data["data"]["catalogs"]
            articles = []
        except RequestException as e:
            logger.error(f"Error fetching Binance announcements: {e}")
            return ret
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return ret

        for catalog in catalogs:
            if catalog.get("catalogId") == 48:
                articles = catalog.get("articles", [])
                break

        for article in articles:
            title = article.get("title", "")
            code = article.get("code", "")
            link = self.get_article_link(title, code)
            release_time = self.timestamp_to_time(article.get("releaseDate", 0))

            article_id = str(article.get("id", ""))
            if not article_id or article_id in cached_articles:
                continue

            if "Launchpool" in title:
                self.process_launchpool(article)
            elif "超级赚币" in title:
                self.process_superearn(article)
            else:
                continue

            item = {"title": title, "link": link, "release_time": release_time}
            ret[article_id] = item
            logger.info(f"Found new Binance article: {title}")

        if not ret:
            logger.debug("Binance: No new Launchpool articles")

        return ret


class BybitLaunchPool:
    def __init__(self):
        self.announce_url = "https://api2.bybit.com/announcements/api/search/v1/index/announcement-posts_zh-my"
        self.headers = {
            "accept": "application/json",
            "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                " AppleWebKit/537.36 (KHTML, like Gecko)"
                " Chrome/129.0.0.0 Safari/537.36"
            ),
            "referer": "https://announcements.bybit.com/",
        }

    def timestamp_to_time(self, timestamp):
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    def process_launchpool(self, article):
        title = 'Bybit Launchpool'
        notify.send(title, article["title"])

    def get_and_process_announces(self, cached_articles):
        ret = {}
        req_data = {"data": {"query": "", "page": 0, "hitsPerPage": 8}}
        try:
            session = requests_retry_session()
            response = session.post(self.announce_url, headers=self.headers, json=req_data, timeout=5)
            response.raise_for_status()
            resp_json = response.json()
            articles = resp_json.get("result", {}).get("hits", [])
        except RequestException as e:
            logger.error(f"Error fetching Bybit announcements: {e}")
            return ret
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return ret

        for article in articles:
            title = article.get("title", "")
            if "Launchpool" not in title:
                continue

            self.process_launchpool(article)
            object_id = article.get("objectID", "")
            if not object_id or object_id in cached_articles:
                continue

            publish_time = self.timestamp_to_time(article.get("publish_time", 0))
            url = urljoin("https://announcements.bybit.com/zh-MY/", article.get("url", ""))
            item = {"title": title, "publish_time": publish_time, "url": url}
            ret[object_id] = item
            logger.info(f"Found new Bybit article: {title}")

        if not ret:
            logger.debug("Bybit: No new Launchpool articles")

        return ret


class LaunchPool:
    def __init__(self):
        self.cache_file = os.path.join(current_dir, "cached_article.json")
        self.load_article_cache()
        self.binance = BinanceLaunchPool()
        self.bybit = BybitLaunchPool()

    def load_article_cache(self):
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self.cache_articles = json.load(f)
            else:
                self.cache_articles = {"binance": {}, "bybit": {}}
        except Exception as e:
            logger.error(f"Error loading cache: {e}")
            self.cache_articles = {"binance": {}, "bybit": {}}

    def save_article_cache(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self.cache_articles, f, ensure_ascii=False, indent=4)
            logger.info(f"Saved article cache to {self.cache_file}")
        except Exception as e:
            logger.error(f"Error saving cache: {e}")

    def binance_task(self):
        cached_articles = self.cache_articles.setdefault("binance", {})
        new_articles = self.binance.get_and_process_announces(cached_articles)
        if new_articles:
            cached_articles.update(new_articles)
            self.save_article_cache()

    def bybit_task(self):
        cached_articles = self.cache_articles.setdefault("bybit", {})
        new_articles = self.bybit.get_and_process_announces(cached_articles)
        if new_articles:
            cached_articles.update(new_articles)
            self.save_article_cache()


def main():
    launch_pool = LaunchPool()
    scheduler = BlockingScheduler()

    scheduler.add_job(launch_pool.binance_task, 'interval', seconds=watch_interval, id='binance_task')
    scheduler.add_job(launch_pool.bybit_task, 'interval', seconds=watch_interval, id='bybit_task')

    try:
        logger.info("Scheduler started. Press Ctrl+C to exit.")
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
    except Exception as e:
        logger.error(f"Error running scheduler: {e}")


if __name__ == "__main__":
    main()
