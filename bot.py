import json
import os
import praw
import psycopg2
import types
import sys

from datetime import datetime
from dhooks import Embed, Webhook
from humanize import naturaltime


class Bot():
  def __init__(self):
    self.webhook = os.environ.get('WEBHOOK', False)
    self.subreddit = os.environ.get('SUBREDDIT', False)
    self.client_id = os.environ.get('CLIENT_ID', False)
    self.client_secret = os.environ.get('CLIENT_SECRET', False)
    self.refresh_token = os.environ.get('REFRESH_TOKEN', False)
    self.user_agent = os.environ.get('USER_AGENT', False)
    self.db_url = os.environ.get('DATABASE_URL', False)

    if (False in (self.webhook, self.subreddit, self.client_id, self.client_secret, self.refresh_token, self.user_agent, self.db_url)):
      print("One or more environment variables are not set. Please navigate to you Heroku app's Settings page and add your Config Vars.")
      sys.exit()

    # optional flag to skip posting to Discord
    # good option for a first-run to populate known reports into the database,
    # and not spam Discord on first run with multiple reports
    self.skip_discord = os.environ.get('SKIP_DISCORD', False)

    self.reddit = praw.Reddit(
      client_id = self.client_id,
      client_secret = self.client_secret,
      refresh_token = self.refresh_token,
      user_agent = self.user_agent
    )

    print("Connecting to the PostgreSQL database...")
    self.conn = psycopg2.connect(self.db_url)
    self.cursor = self.conn.cursor()

    # create the table if it doesn't exist
    self.cursor.execute("CREATE TABLE IF NOT EXISTS public.reports(id int GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, report text NOT NULL)")
    self.conn.commit()


  def __get_report_generator(self, **generator_kwargs):
    return self.reddit.subreddit(self.subreddit).mod.reports(**generator_kwargs)


  def __get_report_url(self, report):
    if isinstance(report.permalink, types.MethodType):
      return "https://www.reddit.com/r/{}{}".format(self.subreddit, report.permalink(fast=True))
    else:
      return "https://www.reddit.com{}".format(report.permalink)


  def __get_report_type(self, report):
    isSubmission = isinstance(report, praw.models.reddit.submission.Submission)
    return "submission" if isSubmission else "comment"


  def __get_submission_from_comment(self, comment):
    ancestor = comment
    refresh_counter = 0
    # get top-level comment of the chain
    while not ancestor.is_root:
      ancestor = ancestor.parent()
      if refresh_counter % 9 == 0:
        ancestor.refresh()
      refresh_counter += 1
    result = ancestor.parent()
    return result


  def __generate_embed(self, report):
    reportType = self.__get_report_type(report)

    embed = Embed(
      color=0xEE4433,
      title="A {} by {} has been reported".format(reportType, report.author),
      url=self.__get_report_url(report),
      timestamp=datetime.fromtimestamp(report.created_utc).isoformat()
    )

    embed.set_author(
      name=report.author.name,
      url="https://reddit.com/u/{}".format(report.author.name),
      icon_url=report.author.icon_img
    )

    # content of the embed
    reportTitle = None
    reportContent = None

    if reportType == "submission":
      reportTitle = report.title
      reportContent = (report.selftext[:120] + '...' if len(report.selftext) > 120 else report.selftext)
    elif reportType == "comment":
      parent = self.__get_submission_from_comment(report)
      reportTitle = parent.title
      reportContent = (report.body[:120] + '...' if len(report.body) > 120 else report.body)
    else:
      reportTitle = '(unknown)'
      reportContent = '(unknown)'

    embed.add_field(
      name = json.dumps(reportTitle),
      value = json.dumps(reportContent)
    )

    # mod reports field
    if len(report.mod_reports) > 0:
      mod_reports = []
      for r in report.mod_reports:
        reason = r[0]
        user = r[1]
        mod_reports.append('{} - {}'.format(reason, user))

      embed.add_field(
        name="Mod Reports",
        value='\n'.join(mod_reports)
      )

    # user reports field
    if len(report.user_reports) > 0:
      user_reports = []
      for r in report.user_reports:
        reason = r[0]
        user = r[1]
        user_reports.append('{} - {}'.format(reason, user))

      embed.add_field(
        name="User Reports",
        value='\n'.join(user_reports)
      )

    # author field
    account_age = datetime.utcnow() - datetime.fromtimestamp(report.author.created_utc)
    account_age_str = naturaltime(account_age)

    field_lines = [
        "Account created {}".format(account_age_str),
        "Link karma: {}".format(report.author.link_karma),
        "Comment karma: {}".format(report.author.comment_karma)
    ]

    embed.add_field(
        name="{} posted by {}".format(reportType.title(), report.author.name),
        value='\n'.join(field_lines)
    )

    # footer
    embed.set_footer(
      text='Powered by PRAW and /u/b0xors',
      icon_url='https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Python-logo-notext.svg/110px-Python-logo-notext.svg.png'
    )

    # thumbnail
    embed.set_thumbnail('http://www.redditstatic.com/new-icon.png')

    return embed


  def __send_to_discord(self, report):
    hook = Webhook(self.webhook)
    embed = self.__generate_embed(report)
    hook.send(embed=embed)


  def __report_exists(self, report):
    self.cursor.execute(
      "SELECT exists(SELECT * FROM public.reports WHERE report='{}')".format(report)
    )
    return self.cursor.fetchone()[0]


  def __save_report(self, report):
    self.cursor.execute(
      "INSERT INTO public.reports(report) VALUES ('{}')".format(report)
    )
    self.conn.commit()


  def run(self):
    print("Starting stream for all reports...")
    for report in praw.models.util.stream_generator(self.__get_report_generator):
      print("Checking report {}...".format(report))
      if (self.__report_exists(report)):
        print("{} has already been reported".format(report))
      else:
        self.__save_report(report)
        if not self.skip_discord:
          self.__send_to_discord(report)
        print("A {} by {} has been reported: <{}>".format(self.__get_report_type(report), report.author, self.__get_report_url(report)))


if __name__ == "__main__":
  Bot().run()
